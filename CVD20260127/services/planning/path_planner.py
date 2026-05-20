# -*- coding: utf-8 -*-
# services/planning/path_planner.py - 路径规划服务
"""
路径规划服务
职责：为wafer规划完整的路径和时间
"""
import time
import logging
from typing import List, Tuple, Optional, Dict

from entities import SchedulingWafer, SchedulingChamber, TransportTask, PathStep, ChamberTask
from config import SystemConfig, ConfigService
from utils import LocationParser,calc_transport_time,is_chamber
from domain import WaferService
from models import module_id_to_str
from common import LogIcon


class PathPlanner:
    """路径规划服务 - 重新设计实现"""

    def __init__(self, system):
        self.system = system

    def plan_wafer_route_from_foup(self, wafer: SchedulingWafer) -> bool:
        """从FOUP规划wafer的完整路径"""
        try:
            remaining_steps = self._get_remaining_steps(wafer)
            if not remaining_steps:
                logging.warning(f"Wafer {wafer.wafer_id} 没有剩余工艺步骤")
                return False

            start_time = time.time()
            path_plan, leave_foup_time, bottleneck_index = self._plan_path_with_timing(  # ✅ 接收bottleneck_index
                wafer, remaining_steps, start_time
            )

            if not path_plan:
                logging.warning(f"Wafer {wafer.wafer_id} 路径规划失败")
                return False

            # ✅ 提前时间
            advance_time = SystemConfig.SWITCH_ADVANCE_TIME
            if bottleneck_index >= 0 and (leave_foup_time - start_time) > advance_time:
                logging.info(f"Wafer {wafer.wafer_id}: 瓶颈在工艺{path_plan[bottleneck_index].location_id}, 提前{advance_time}s出发以switch")
                for i in range(bottleneck_index):
                    path_plan[i].estimated_arrival -= advance_time
                    path_plan[i].estimated_departure -= advance_time
                leave_foup_time -= advance_time

            # 保存规划结果
            wafer.path_plan = path_plan
            wafer.current_step_index = 0
            wafer.scheduled_leave_time = leave_foup_time

            # 创建传输任务队列
            self._build_assignment_queue(wafer, path_plan)

            # 预订Chamber
            self._reserve_chamber_slots(wafer, path_plan)

            # 日志输出
            self._log_plan_result(wafer, path_plan, start_time, leave_foup_time)

            return True

        except Exception as e:
            logging.error(f"{LogIcon.ERROR} Wafer {wafer.wafer_id} 路径规划错误: {e}")
            return False
    def calculate_leave_time_without_reservation(self, wafer_base) -> float:
        """
        仅计算wafer离开FOUP的时间，不预订任何资源

        Returns:
            leave_time: 预计离开FOUP的时间
        """
        from entities import SchedulingWafer

        # 创建临时wafer对象（不添加到system）
        temp_wafer = SchedulingWafer(wafer_base)
        temp_wafer.current_location_id = WaferService.get_foup_location_id(temp_wafer)

        # 获取剩余步骤
        remaining_steps = self._get_remaining_steps(temp_wafer)
        if not remaining_steps:
            return float('inf')

        # 只计算路径和时间，不调用 _reserve_chamber_slots
        path_plan, leave_time, _ = self._plan_path_with_timing(
            temp_wafer, remaining_steps, time.time()
        )

        return leave_time if path_plan else float('inf')

    def _get_remaining_steps(self, wafer: SchedulingWafer) -> List[Dict]:
        """获取剩余工艺步骤"""
        sequence = wafer.base.associated_sequence
        remaining = []

        for i in range(wafer.current_step_index + 1, len(sequence.sequence_steps)):
            step = sequence.sequence_steps[i]
            if step.target_modules:
                process_type = ConfigService.get_process_type(
                    module_id_to_str(step.target_modules[0])
                )
                remaining.append({'step': step, 'process_type': process_type})

        return remaining

    def _plan_path_with_timing(self, wafer: SchedulingWafer, remaining_steps: List[Dict],
                               start_time: float) -> Tuple[List[PathStep], float, int]:
        """规划路径并计算时间"""
        leave_foup_time = start_time
        relative_time = 0.0
        current_location = WaferService.get_foup_location_id(wafer)
        temp_path = []

        bottleneck_index = -1  # ✅ 记录瓶颈索引

        for step_idx, step_info in enumerate(remaining_steps):  # ✅ 添加enumerate
            process_type = step_info['process_type']
            step = step_info['step']

            # 选择位置
            location_id = self._select_location_for_step(wafer, step, process_type)

            if not location_id and process_type not in ['TBS', 'LL']:
                logging.error(f"无法为工艺 {process_type} 选择位置")
                return [], 0.0, -1  # ✅ 返回-1

            # ✅ 检查Chamber约束并记录瓶颈
            if ConfigService.is_chamber_module(LocationParser.get_base_module_id(location_id) if location_id else ''):
                chamber = self.system.chambers.get(location_id)
                if chamber:
                    chamber_ready_time = self._get_chamber_ready_time(chamber)
                    wafer_ready_time = leave_foup_time + relative_time

                    if wafer_ready_time < chamber_ready_time:
                        required_delay = chamber_ready_time - wafer_ready_time
                        leave_foup_time += required_delay
                        if chamber.task_queue:
                            if chamber.task_queue[-1].task_type == 'wafer_process':
                                # 记录瓶颈。如果chamber是因为宏任务阻塞生产，不记录，因为记录瓶颈是为了在瓶颈处switch。无wafer则不存在switch。
                                bottleneck_index = step_idx
                            else:
                                bottleneck_index = -1
                        elif chamber.current_task:
                            if chamber.current_task.task_type == 'wafer_process':
                                bottleneck_index = step_idx
                            else:
                                bottleneck_index = -1

            # 计算时间
            transport_time = calc_transport_time(current_location, location_id)
            process_time = ConfigService.get_process_time(process_type)

            # 添加到临时路径
            temp_path.append({
                'process_type': process_type,
                'location_id': location_id,
                'relative_arrival': relative_time + transport_time,
                'relative_departure': relative_time + transport_time + process_time
            })

            relative_time += transport_time + process_time

            current_location = location_id

        # ✅ 返回瓶颈索引
        return (self._convert_to_path_steps(temp_path, leave_foup_time),
                leave_foup_time,
                bottleneck_index)

    def _select_location_for_step(self, wafer: SchedulingWafer, step, process_type: str) -> Optional[str]:
        """为工艺步骤选择位置"""
        if process_type in ['TBS', 'LL']:
            return ''  # 动态选择
        elif process_type == 'FOUP':
            return WaferService.get_foup_location_id(wafer)
        elif process_type == 'ALIGNER':
            return self._find_aligner_for_wafer(wafer)
        else:
            # Chamber
            return self._find_best_chamber(step.target_modules)

    def _find_aligner_for_wafer(self, wafer: SchedulingWafer) -> Optional[str]:
        """根据wafer angle查找Aligner (CVD: 不使用槽位后缀)"""
        angle = wafer.base.angle.value
        for aligner_id, config in SystemConfig.ALIGNER_MODULES.items():
            if config['angle'] == angle:
                # CVD: 直接返回模块名，不添加_1后缀
                return aligner_id
        return None

    def _find_best_chamber(self, target_modules) -> Optional[str]:
        """查找最佳Chamber（最早可用）"""
        candidate_chambers = [
            c for c in self.system.chambers.values()
            if any(LocationParser.get_base_module_id(c.location_id) == module_id_to_str(mid)
                   for mid in target_modules) and c.base.online == 1
        ]

        if not candidate_chambers:
            return None

        # 选择最早就绪的Chamber
        best_chamber = min(candidate_chambers,
                           key=lambda c: self._get_chamber_ready_time(c))
        return best_chamber.location_id

    def _get_chamber_ready_time(self, chamber: SchedulingChamber) -> float:
        """计算Chamber何时就绪"""
        current_time = time.time()

        # 空闲且无队列
        if chamber.busy == 0 and not chamber.task_queue and not chamber.current_task:
            return current_time

        # 计算当前任务结束时间
        ready_time = current_time
        if chamber.current_task and chamber.task_start_time:
            remaining = chamber.current_task.duration - (current_time - chamber.task_start_time)
            ready_time += max(remaining, 0)

        # 累加队列中的任务时间
        for task in chamber.task_queue:
            if task.task_type == 'wafer_process':
                # Wafer任务：从其path_plan获取预计时间
                wafer = self.system.wafers.get(task.wafer_id)
                if wafer and wafer.path_plan:
                    for path_step in wafer.path_plan:
                        if path_step.location_id == chamber.location_id:
                            if path_step.estimated_departure >= ready_time:
                                ready_time = path_step.estimated_departure
                            else:
                                logging.error(f'{wafer.wafer_id} 在 {chamber.location_id}: 时间异常')
                            break
                else:
                    logging.error('wafer没有path plan')
            elif task.task_type == 'macro':
                ready_time += task.duration

        return ready_time


    def _convert_to_path_steps(self, temp_path: List[Dict], leave_foup_time: float) -> List[PathStep]:
        """将临时路径转换为PathStep列表"""
        return [
            PathStep(
                process_type=step_data['process_type'],
                location_id=step_data['location_id'],
                estimated_arrival=leave_foup_time + step_data['relative_arrival'],
                estimated_departure=leave_foup_time + step_data['relative_departure']
            )
            for step_data in temp_path
        ]

    def _build_assignment_queue(self, wafer: SchedulingWafer, path_plan: List[PathStep]):
        """构建传输任务队列"""
        wafer.assignment_queue = []
        current_location = wafer.current_location_id

        for step in path_plan:
            wafer.assignment_queue.append(TransportTask(
                wafer_id=wafer.wafer_id,
                from_location=current_location,
                to_location=step.location_id
            ))
            current_location = step.location_id

    def _reserve_chamber_slots(self, wafer: SchedulingWafer, path_plan: List[PathStep]):
        """预订Chamber时间槽"""
        for step in path_plan:
            if not step.location_id or step.location_id not in self.system.chambers:
                continue

            chamber = self.system.chambers[step.location_id]
            process_time = ConfigService.get_process_time(step.process_type, 120.0)

            chamber.task_queue.append(ChamberTask(
                task_id=f"reserve_{wafer.wafer_id}_{step.location_id}",
                task_type='wafer_process',
                wafer_id=wafer.wafer_id,
                duration=process_time
            ))

    def _log_plan_result(self, wafer: SchedulingWafer, path_plan: List[PathStep],
                         start_time: float, leave_foup_time: float):
        """输出规划结果日志"""
        logging.info(f"{LogIcon.PLAN} 路径规划: Wafer {wafer.wafer_id}: "
                     f"计划 {max(leave_foup_time - start_time, 0):.1f}s 后离开FOUP")

        for i, step in enumerate(path_plan):
            arrival = time.strftime('%H:%M:%S', time.localtime(step.estimated_arrival))
            departure = time.strftime('%H:%M:%S', time.localtime(step.estimated_departure))
            location = step.location_id if step.location_id else '待定'
            logging.info(f"  {i}. {step.process_type:8} @ {location:15} {arrival} → {departure}")

    def update_path_if_delayed(self, wafer: SchedulingWafer, delay: float):
        """更新路径规划（传播延迟，带截止条件）"""
        if delay <= 2:
            return

        logging.info(f"Wafer{wafer.wafer_id} 延迟 {delay:.1f}s, 更新后续path plan")

        # 更新当前wafer，从当前步骤开始（wafer已到达）
        start_index = wafer.current_step_index - 1
        stop_index = self._propagate_delay(wafer, start_index, delay)

        # 打印更新后的计划
        self._log_updated_plan(wafer, start_index)

        # 级联更新后续wafer
        self.update_affected_wafers_in_chambers(wafer, stop_index)

    def _propagate_delay(self, wafer: SchedulingWafer, start_index: int, delay: float) -> int:
        """
        传播延迟到wafer的后续步骤

        Args:
            wafer: 要更新的wafer
            start_index: 从哪个步骤开始更新（被延迟影响的步骤）
            delay: 延迟时间

        Returns:
            int: 停止传播的索引（延迟被吸收的位置）
        """
        # 更新起始步骤
        start_step = wafer.path_plan[start_index]
        start_step.estimated_arrival += delay
        start_step.estimated_departure += delay

        # 从下一步开始检查是否需要传播
        for i in range(start_index + 1, len(wafer.path_plan)):
            step = wafer.path_plan[i]

            # 计算传输时间
            from_loc = wafer.path_plan[i - 1].location_id
            transport_time = calc_transport_time(from_loc, step.location_id)

            # 计算最早到达时间
            prev_step = wafer.path_plan[i - 1]
            earliest_arrival = prev_step.estimated_departure + transport_time

            # 终止条件：延迟被吸收
            if earliest_arrival < step.estimated_arrival:
                logging.info(f"  Wafer{wafer.wafer_id}延迟在步骤{i}被吸收")
                return i  # 返回停止索引

            # 传播延迟
            step.estimated_arrival += delay
            step.estimated_departure += delay

        return len(wafer.path_plan)  # 传播到最后

    def update_affected_wafers_in_chambers(self, wafer: SchedulingWafer, stop_index: int):
        """级联更新Chamber队列中受影响的wafer"""
        from utils import is_chamber

        to_process = [(wafer, wafer.current_step_index - 1, stop_index)]
        processed = set()

        while to_process:
            current_wafer, start_idx, current_stop_idx = to_process.pop(0)

            if current_wafer.wafer_id in processed:
                continue
            processed.add(current_wafer.wafer_id)

            # 遍历当前wafer的chamber步骤（只到stop_index）
            for step_idx, step in enumerate(current_wafer.path_plan[start_idx:current_stop_idx], start=start_idx):
                if not is_chamber(step.location_id):
                    continue

                chamber = self.system.chambers.get(step.location_id)
                if not chamber:
                    continue

                # 找到当前wafer在队列中的位置
                my_index = next((i for i, t in enumerate(chamber.task_queue)
                                 if t.wafer_id == current_wafer.wafer_id), -1)
                if my_index == -1:
                    continue

                # 更新后续wafer
                for task in chamber.task_queue[my_index + 1:]:
                    if task.task_type != 'wafer_process':
                        continue

                    other_wafer = self.system.wafers.get(task.wafer_id)
                    if not other_wafer:
                        continue

                    # 找到other_wafer在该chamber的步骤索引
                    other_idx = next((i for i, s in enumerate(other_wafer.path_plan)
                                      if s.location_id == step.location_id), None)
                    if other_idx is None:
                        continue

                    # 计算时间差
                    other_step = other_wafer.path_plan[other_idx]
                    new_arrival = step.estimated_departure + calc_transport_time(
                        other_wafer.path_plan[other_idx - 1].location_id if other_idx > 0 else '',
                        step.location_id
                    )

                    time_diff = max(0, new_arrival - other_step.estimated_arrival)

                    # 传播延迟（带截止条件）
                    if time_diff > 2:
                        # 打印级联更新的wafer的新计划
                        logging.info(f"  级联更新 Wafer{other_wafer.wafer_id} (延迟 {time_diff:.1f}s)")
                        # ✅ 从other_idx开始更新other_wafer
                        other_stop_idx = self._propagate_delay(other_wafer, other_idx, time_diff)
                        self._log_updated_plan(other_wafer, other_idx)

                        to_process.append((other_wafer, other_idx, other_stop_idx))

    def _log_updated_plan(self, wafer: SchedulingWafer, start_index: int):
        """打印更新后的计划"""
        logging.info(f"  Wafer{wafer.wafer_id} 更新后的计划:")
        for i in range(start_index, len(wafer.path_plan)):
            step = wafer.path_plan[i]
            arrival = time.strftime('%H:%M:%S', time.localtime(step.estimated_arrival))
            departure = time.strftime('%H:%M:%S', time.localtime(step.estimated_departure))
            location = step.location_id if step.location_id else '待定'
            logging.info(f"    {i}. {step.process_type:8} @ {location:15} {arrival} → {departure}")
