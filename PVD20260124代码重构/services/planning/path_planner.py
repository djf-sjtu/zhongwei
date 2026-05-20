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
from utils import LocationParser
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
            path_plan, leave_foup_time = self._plan_path_with_timing(wafer, remaining_steps, start_time)
            
            if not path_plan:
                logging.warning(f"Wafer {wafer.wafer_id} 路径规划失败")
                return False

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
        path_plan, leave_time = self._plan_path_with_timing(
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
                                start_time: float) -> Tuple[List[PathStep], float]:
        """规划路径并计算时间"""
        leave_foup_time = start_time
        relative_time = 0.0
        current_location = WaferService.get_foup_location_id(wafer)
        temp_path = []

        for step_info in remaining_steps:
            process_type = step_info['process_type']
            step = step_info['step']

            # 选择位置
            location_id = self._select_location_for_step(wafer, step, process_type)
            
            if not location_id and process_type not in ['TBS', 'LL']:
                logging.error(f"无法为工艺 {process_type} 选择位置")
                return [], 0.0

            # 检查Chamber约束并调整离开时间
            if ConfigService.is_chamber_module(LocationParser.get_base_module_id(location_id) if location_id else ''):
                leave_foup_time = self._adjust_leave_time_for_chamber(
                    location_id, leave_foup_time, relative_time
                )

            # 计算时间
            transport_time = self._calc_transport_time(current_location, location_id or '')
            process_time = self._calc_process_time(process_type, step, current_location)

            # 添加到临时路径
            temp_path.append({
                'process_type': process_type,
                'location_id': location_id,
                'relative_arrival': relative_time + transport_time,
                'relative_departure': relative_time + transport_time + process_time
            })

            relative_time += transport_time + process_time
            current_location = location_id or ''

        # 转换为绝对时间的PathStep
        return self._convert_to_path_steps(temp_path, leave_foup_time), leave_foup_time

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
        """根据wafer angle查找Aligner"""
        angle = wafer.base.angle.value
        for aligner_id, config in SystemConfig.ALIGNER_MODULES.items():
            if config['angle'] == angle:
                return f"{aligner_id}_1"
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

    def _adjust_leave_time_for_chamber(self, chamber_id: str, leave_foup_time: float,
                                       relative_time: float) -> float:
        """调整离开FOUP时间以匹配Chamber就绪时间"""
        chamber = self.system.chambers.get(chamber_id)
        if not chamber:
            return leave_foup_time

        chamber_ready_time = self._get_chamber_ready_time(chamber)
        wafer_ready_time = leave_foup_time + relative_time

        if wafer_ready_time < chamber_ready_time:
            required_delay = chamber_ready_time - wafer_ready_time
            return leave_foup_time + required_delay

        return leave_foup_time

    def _calc_transport_time(self, from_loc: str, to_loc: str) -> float:
        """计算传输时间"""
        base_time = SystemConfig.TRANSPORT_BASE_TIME + SystemConfig.PICK_PLACE_TIME * 2

        if from_loc:
            from_z = LocationParser.get_z_level_for_location(from_loc)
            to_z = LocationParser.get_z_level_for_location(to_loc) if to_loc else 'LL_TBS'
            
            if from_z != to_z:
                base_time += SystemConfig.Z_MOVE_TIME * 2

        return base_time

    def _calc_process_time(self, process_type: str, step, current_location: str = '') -> float:
        """计算工艺处理时间"""
        if process_type == 'TBS':
            # TMB区域需要冷却
            if current_location:
                base_module = LocationParser.get_base_module_id(current_location)
                zone = ConfigService.get_module_zone(base_module)
                if zone == 'TMB':
                    return SystemConfig.COOLING_TIME_SECONDS
            return 0

        if process_type == 'FOUP':
            return 0

        if process_type == 'LL':
            return SystemConfig.DRY_PUMP_CONFIG['pump_time_seconds']

        # 从Recipe获取
        recipe = step.process_recipe
        if recipe and recipe.recipe_views:
            return recipe.recipe_views[0].recipe_max_time_seconds

        # 默认配置
        return ConfigService.get_process_time(process_type, 60.0)

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
