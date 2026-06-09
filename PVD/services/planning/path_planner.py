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
from utils import LocationParser, is_ll, is_tbs, is_foup, is_chamber
from models import module_id_to_str
from common import LogIcon, trace_writer


class PathPlanner:
    """路径规划服务 - 重新设计实现"""

    def __init__(self, system):
        self.system = system
        self.metrics = None

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

            # 等价性 trace：路径规划事件（仅决策内容，不含 wall-clock 时间）
            trace_writer.emit(
                'path_planned',
                wafer_id=wafer.wafer_id,
                plan=[
                    {
                        'step_idx': i,
                        'process_type': step.process_type,
                        'location_id': step.location_id or '',
                    }
                    for i, step in enumerate(path_plan)
                ],
            )

            return True

        except Exception as e:
            logging.error(f"{LogIcon.ERROR} Wafer {wafer.wafer_id} 路径规划错误: {e}")
            return False

    def replan_for_faulted_chamber(
        self, wafer: SchedulingWafer, faulted_chamber_id: str
    ) -> bool:
        """
        把 wafer.path_plan 中下游 location_id == faulted_chamber_id 的那一步换成
        同 process_type 的其他可用 chamber，重算从该 step 起的 estimated_arrival/
        departure，更新 chamber.task_queue 上的预订和 wafer.assignment_queue。

        v1 简化：
        - per-step 替换（只换故障那一步，下游其他 step 的 chamber 不动）
        - 替换后 reservation append 到新 chamber.task_queue 末尾
        - 找不到替代 → 返回 False（v1 不处理，留给 #03 临停 issue）

        Returns:
            True 重规划成功；False 找不到替代 chamber 或下游不含故障 chamber
        """
        # 1. 找下游故障 step（跳过 current_step_index 之前的）
        fault_step_idx = None
        for i in range(wafer.current_step_index, len(wafer.path_plan)):
            if wafer.path_plan[i].location_id == faulted_chamber_id:
                fault_step_idx = i
                break
        if fault_step_idx is None:
            return False  # 下游不含故障 chamber，无需重规划

        # 2. 找替代 chamber Y'
        seq_steps = wafer.base.associated_sequence.sequence_steps
        fault_process_type = wafer.path_plan[fault_step_idx].process_type
        new_chamber_id = self._find_best_chamber_for_type(fault_process_type)

        if not new_chamber_id:
            return self.temp_park_wafer(wafer, fault_step_idx)
        if new_chamber_id == faulted_chamber_id:
            return False  # 当前 chamber 已是最优，无需切换

        # 3. 重算从 fault_step_idx 起的时序（per-step replace + cascade）
        prev_step = wafer.path_plan[fault_step_idx - 1]
        start_time = max(prev_step.estimated_departure, time.time())

        relative_time = 0.0
        current_location = prev_step.location_id
        # 收集 (path_idx, new_location, new_arrival, new_departure)
        new_segments = []

        for i in range(fault_step_idx, len(wafer.path_plan)):
            ptype = wafer.path_plan[i].process_type
            # 按 process_type 找对应 seq_step（LL/TBS/FOUP/ALIGNER 无对应 seq_step，返回 None）
            step = next(
                (s for s in seq_steps if s.target_modules and
                 ConfigService.get_process_type(module_id_to_str(s.target_modules[0])) == ptype),
                None
            )

            if i == fault_step_idx:
                location_id = new_chamber_id
            else:
                # 下游其他 step 保留原 path_plan 中的 location_id
                location_id = wafer.path_plan[i].location_id

            transport_time = self._calc_transport_time(
                current_location, location_id or ''
            )
            process_time = self._calc_process_time(ptype, step, current_location)

            est_arrival = start_time + relative_time + transport_time
            est_departure = est_arrival + process_time

            new_segments.append((i, location_id, est_arrival, est_departure))

            relative_time += transport_time + process_time
            current_location = location_id or ''

        # 4. 清掉故障 chamber X 上为该 wafer 的预订
        faulted_chamber = self.system.chambers.get(faulted_chamber_id)
        if faulted_chamber:
            faulted_chamber.task_queue = [
                t for t in faulted_chamber.task_queue
                if not (
                    t.task_type == 'wafer_process'
                    and t.wafer_id == wafer.wafer_id
                    and isinstance(t.task_id, str)
                    and t.task_id.startswith('reserve_')
                )
            ]

        # 5. 在新 chamber Y' 上 append reservation（带 estimated_arrival）
        new_chamber = self.system.chambers[new_chamber_id]
        new_process_time = ConfigService.get_process_time(
            wafer.path_plan[fault_step_idx].process_type, 120.0
        )
        # 从 new_segments 中取出新 fault_step 的 estimated_arrival
        new_arrival = next(
            (arr for idx, _, arr, _ in new_segments if idx == fault_step_idx), 0.0
        )
        new_task = ChamberTask(
            task_id=f"reserve_{wafer.wafer_id}_{new_chamber_id}",
            task_type='wafer_process',
            wafer_id=wafer.wafer_id,
            duration=new_process_time,
            estimated_arrival=new_arrival,
        )
        # 按 estimated_arrival 升序插入，保证队列顺序与实际到达顺序一致
        insert_pos = len(new_chamber.task_queue)
        for i, t in enumerate(new_chamber.task_queue):
            if t.estimated_arrival > 0 and new_arrival > 0 and t.estimated_arrival > new_arrival:
                insert_pos = i
                break
        new_chamber.task_queue.insert(insert_pos, new_task)

        # 6. 应用新 path_plan（in-place 改字段，保持 PathStep 对象引用稳定）
        for path_idx, loc_id, est_arr, est_dep in new_segments:
            wafer.path_plan[path_idx].location_id = loc_id
            wafer.path_plan[path_idx].estimated_arrival = est_arr
            wafer.path_plan[path_idx].estimated_departure = est_dep

        # 7. 更新 wafer.assignment_queue 里涉及 X 的 task。
        # assignment_queue 中的 TransportTask 同时被 robot.transport_queue 引用，
        # 改字段会同步反映到 robot 队列。
        for task in wafer.assignment_queue:
            if task.to_location == faulted_chamber_id:
                task.to_location = new_chamber_id
            if task.from_location == faulted_chamber_id:
                task.from_location = new_chamber_id

        # 8. emit wafer_rerouted trace 事件
        new_plan = [
            {
                'step_idx': i,
                'process_type': wafer.path_plan[i].process_type,
                'location_id': wafer.path_plan[i].location_id or '',
            }
            for i in range(len(wafer.path_plan))
        ]
        trace_writer.emit(
            'wafer_rerouted',
            wafer_id=wafer.wafer_id,
            new_plan=new_plan,
        )
        if self.metrics:
            self.metrics.on_rerouted()

        logging.info(
            f"{LogIcon.PLAN} 重规划: Wafer {wafer.wafer_id} 故障 chamber "
            f"{faulted_chamber_id} → {new_chamber_id} (step {fault_step_idx})"
        )
        return True

    def replan_process_type_for_fault(self, process_type: str, faulted_chamber_id: str) -> int:
        """Replan all safe in-system wafers that have not reached a process type yet."""
        return self._replan_process_type_pool(
            process_type=process_type,
            blocked_chamber_id=faulted_chamber_id,
            reason='fault',
        )

    def rebalance_process_type_after_unfault(self, process_type: str) -> int:
        """Rebalance future reservations after capacity is restored."""
        return self._replan_process_type_pool(
            process_type=process_type,
            blocked_chamber_id=None,
            reason='unfault',
        )

    def collect_process_replan_candidates(self, process_type: str, blocked_chamber_id: str = None) -> list:
        """Collect wafers with a future step for process_type.

        Wafers already inside the blocked chamber are intentionally excluded; their
        behavior is handled by fault freeze/unfault semantics, not by this replan.
        """
        candidates = []
        for wafer in self.system.wafers.values():
            if blocked_chamber_id and wafer.current_location_id == blocked_chamber_id:
                continue
            active_transport = wafer.active_transport_task
            if active_transport and active_transport.to_location == blocked_chamber_id:
                continue
            step_idx = self._find_future_process_step_index(wafer, process_type)
            if step_idx is None:
                continue
            candidates.append((wafer, step_idx))
        return candidates

    def _replan_process_type_pool(self, process_type: str, blocked_chamber_id: str, reason: str) -> int:
        candidates = self.collect_process_replan_candidates(process_type, blocked_chamber_id)
        if self.metrics and blocked_chamber_id:
            self.metrics.on_fault_candidates(
                blocked_chamber_id,
                [wafer.wafer_id for wafer, _ in candidates],
            )

        if not candidates:
            return 0

        replanned = self._replan_remaining_routes(candidates, reason)
        logging.info(
            f"{LogIcon.PLAN} {reason} 剩余路径重规划: process={process_type}, "
            f"候选={len(candidates)}, 成功={replanned}"
        )
        return replanned

    def _replan_remaining_routes(self, candidates: list, reason: str) -> int:
        """Reuse the normal planner from each wafer's earliest mutable boundary."""
        candidate_ids = {wafer.wafer_id for wafer, _ in candidates}
        self._remove_replanned_reservations(candidates)
        self._remove_replanned_robot_tasks(candidate_ids)

        replanned = 0
        for wafer, fault_step_idx in sorted(candidates, key=self._remaining_route_priority):
            context = self._get_remaining_route_context(wafer)
            remaining_steps = self._get_remaining_steps_after_committed(
                wafer, context['committed_steps']
            )
            if not remaining_steps:
                continue

            old_suffix = wafer.path_plan[context['suffix_idx']:]
            new_suffix, plan_start_time = self._plan_path_with_timing(
                wafer,
                remaining_steps,
                context['release_time'],
                start_location=context['start_location'],
                can_delay_start=context['can_delay_start'],
            )
            if not new_suffix:
                if not wafer.active_transport_task and wafer.busy == 0:
                    self.temp_park_wafer(wafer, fault_step_idx)
                continue

            self._apply_remaining_route_plan(
                wafer,
                context,
                old_suffix,
                new_suffix,
                plan_start_time,
                reason,
            )
            replanned += 1
        return replanned

    def _remove_replanned_reservations(self, candidates: list):
        candidate_ids = {wafer.wafer_id for wafer, _ in candidates}
        preserved = {
            (wafer.wafer_id, wafer.active_transport_task.to_location)
            for wafer, _ in candidates
            if wafer.active_transport_task
            and is_chamber(wafer.active_transport_task.to_location)
        }
        for chamber in self.system.chambers.values():
            kept_preserved = set()
            retained = []
            for task in chamber.task_queue:
                is_candidate_reserve = (
                    task.wafer_id in candidate_ids
                    and task.task_type == 'wafer_process'
                    and isinstance(task.task_id, str)
                    and task.task_id.startswith('reserve_')
                )
                key = (task.wafer_id, chamber.location_id)
                if is_candidate_reserve and key in preserved and key not in kept_preserved:
                    kept_preserved.add(key)
                    retained.append(task)
                elif not is_candidate_reserve:
                    retained.append(task)
            chamber.task_queue = retained

    def _remove_replanned_robot_tasks(self, candidate_ids: set):
        for robot in self.system.robots.values():
            robot.transport_queue = [
                task for task in robot.transport_queue
                if (
                    task.wafer_id not in candidate_ids
                    or task is self.system.wafers[task.wafer_id].active_transport_task
                )
            ]

    def _remaining_route_priority(self, item):
        wafer, fault_step_idx = item
        context = self._get_remaining_route_context(wafer)
        return (
            context['priority'],
            context['release_time'],
            max(fault_step_idx - wafer.current_step_index, 0),
            wafer.wafer_id,
        )

    def _get_remaining_route_context(self, wafer: SchedulingWafer) -> dict:
        now = time.time()
        active_transport = wafer.active_transport_task
        if active_transport:
            destination_idx = wafer.current_step_index
            destination_step = wafer.path_plan[destination_idx]
            destination_step.location_id = active_transport.to_location
            arrival_time = max(wafer.active_transport_eta or now, now)
            sequence_step = self._find_sequence_step_for_process(
                wafer, destination_step.process_type
            )
            release_time = max(
                destination_step.estimated_departure,
                arrival_time + self._calc_process_time(
                    destination_step.process_type,
                    sequence_step,
                    active_transport.from_location,
                ),
            )
            destination_is_safe = self._is_safe_hold(
                destination_step.process_type, active_transport.to_location
            )
            return {
                'priority': 1 if is_chamber(active_transport.to_location) else 2,
                'suffix_idx': destination_idx + 1,
                'committed_steps': 1,
                'start_location': active_transport.to_location,
                'release_time': release_time,
                'can_delay_start': destination_is_safe,
                'hold_step_idx': destination_idx,
            }

        if wafer.current_step_index > 0:
            wafer.path_plan[wafer.current_step_index - 1].location_id = wafer.current_location_id

        current_chamber = self.system.chambers.get(wafer.current_location_id)
        if current_chamber:
            release_time = now
            if (
                    current_chamber.current_task
                    and current_chamber.current_task.wafer_id == wafer.wafer_id
                    and current_chamber.task_start_time):
                elapsed = now - current_chamber.task_start_time
                release_time += max(current_chamber.current_task.duration - elapsed, 0.0)
            return {
                'priority': 0,
                'suffix_idx': wafer.current_step_index,
                'committed_steps': 0,
                'start_location': wafer.current_location_id,
                'release_time': release_time,
                'can_delay_start': False,
                'hold_step_idx': (
                    wafer.current_step_index - 1
                    if wafer.current_step_index > 0 else None
                ),
            }

        current_is_safe = self._is_safe_hold('', wafer.current_location_id)
        release_time = now
        hold_step_idx = (
            wafer.current_step_index - 1
            if current_is_safe and wafer.current_step_index > 0
            else None
        )
        if wafer.busy == 1 and hold_step_idx is not None:
            release_time = max(
                release_time,
                wafer.path_plan[hold_step_idx].estimated_departure,
            )
        return {
            'priority': 3 if current_is_safe else 2,
            'suffix_idx': wafer.current_step_index,
            'committed_steps': 0,
            'start_location': wafer.current_location_id,
            'release_time': release_time,
            'can_delay_start': current_is_safe,
            'hold_step_idx': hold_step_idx,
        }

    def _get_remaining_steps_after_committed(
            self, wafer: SchedulingWafer, committed_steps: int) -> List[Dict]:
        sequence = wafer.base.associated_sequence
        adjusted_current = wafer.current_step_index - wafer._temp_park_count
        start_idx = adjusted_current + 1 + committed_steps
        remaining = []
        for i in range(start_idx, len(sequence.sequence_steps)):
            step = sequence.sequence_steps[i]
            if step.target_modules:
                remaining.append({
                    'step': step,
                    'process_type': ConfigService.get_process_type(
                        module_id_to_str(step.target_modules[0])
                    ),
                })
        return remaining

    def _apply_remaining_route_plan(
            self,
            wafer: SchedulingWafer,
            context: dict,
            old_suffix: list,
            new_suffix: list,
            plan_start_time: float,
            reason: str):
        suffix_idx = context['suffix_idx']
        active_transport = wafer.active_transport_task
        prefix = wafer.path_plan[:suffix_idx]
        wafer.path_plan = prefix + new_suffix

        if active_transport:
            wafer.assignment_queue = [active_transport] + self._build_transport_tasks(
                wafer.wafer_id, context['start_location'], new_suffix
            )
        else:
            wafer.assignment_queue = self._build_transport_tasks(
                wafer.wafer_id, context['start_location'], new_suffix
            )

        wafer.transport_not_before = {
            step_idx: gate
            for step_idx, gate in wafer.transport_not_before.items()
            if step_idx < suffix_idx
        }
        first_transport_start = plan_start_time
        if new_suffix:
            first_transport_start = (
                new_suffix[0].estimated_arrival
                - self._calc_transport_time(context['start_location'], new_suffix[0].location_id)
            )
        if first_transport_start > context['release_time'] + 0.001:
            hold_step_idx = context['hold_step_idx']
            if hold_step_idx is not None:
                wafer.path_plan[hold_step_idx].estimated_departure = first_transport_start
            wafer.transport_not_before[suffix_idx] = first_transport_start
            if is_foup(context['start_location']):
                wafer.scheduled_leave_time = first_transport_start
            if self.metrics:
                protected_chamber = next(
                    (step.location_id for step in new_suffix if is_chamber(step.location_id)),
                    '',
                )
                self.metrics.on_timing_replan(
                    wafer.wafer_id,
                    context['start_location'],
                    protected_chamber,
                    first_transport_start - context['release_time'],
                    plan_change={
                        'transport_step_idx': suffix_idx,
                        'hold_departure_before': context['release_time'],
                        'hold_departure_after': first_transport_start,
                    },
                )

        self._reserve_chamber_slots(wafer, new_suffix)
        if wafer.busy == 0 and not active_transport and wafer not in self.system.waiting_wafers:
            self.system.waiting_wafers.append(wafer)

        old_locations = [step.location_id for step in old_suffix]
        new_locations = [step.location_id for step in new_suffix]
        if old_locations != new_locations:
            if self.metrics:
                self.metrics.on_rerouted()
            trace_writer.emit(
                'wafer_rerouted',
                wafer_id=wafer.wafer_id,
                new_plan=[
                    {
                        'step_idx': i,
                        'process_type': step.process_type,
                        'location_id': step.location_id or '',
                    }
                    for i, step in enumerate(wafer.path_plan)
                ],
            )
        trace_writer.emit(
            'wafer_remaining_route_replanned',
            wafer_id=wafer.wafer_id,
            reason=reason,
            suffix_idx=suffix_idx,
        )

    def _build_transport_tasks(
            self, wafer_id: int, start_location: str, path_plan: List[PathStep]) -> list:
        tasks = []
        current_location = start_location
        for step in path_plan:
            tasks.append(TransportTask(
                wafer_id=wafer_id,
                from_location=current_location,
                to_location=step.location_id,
            ))
            current_location = step.location_id
        return tasks

    def _find_future_process_step_index(self, wafer: SchedulingWafer, process_type: str):
        if not wafer.path_plan:
            return None
        for i in range(wafer.current_step_index, len(wafer.path_plan)):
            if wafer.path_plan[i].process_type == process_type:
                return i
        return None

    def _get_chambers_for_process(self, process_type: str) -> list:
        module_ids = ConfigService.get_modules_by_process_type(process_type)
        return [
            chamber for chamber in self.system.chambers.values()
            if LocationParser.get_base_module_id(chamber.location_id) in module_ids
        ]

    def _get_available_chambers_for_process(self, process_type: str, blocked_chamber_id: str = None) -> list:
        return [
            chamber for chamber in self._get_chambers_for_process(process_type)
            if chamber.base.online == 1
            and not chamber.is_faulted
            and chamber.location_id != blocked_chamber_id
        ]

    def _is_safe_hold(self, process_type: str, location_id: str) -> bool:
        return (
            process_type in ('LL', 'TBS', 'FOUP')
            or is_ll(location_id)
            or is_tbs(location_id)
            or is_foup(location_id)
        )

    def _find_sequence_step_for_process(self, wafer, process_type: str):
        seq_steps = wafer.base.associated_sequence.sequence_steps
        return next(
            (step for step in seq_steps
             if step.target_modules
             and ConfigService.get_process_type(module_id_to_str(step.target_modules[0])) == process_type),
            None,
        )

    def calculate_leave_time_without_reservation(self, wafer_base) -> float:
        """
        仅计算wafer离开FOUP的时间，不预订任何资源

        Returns:
            leave_time: 预计离开FOUP的时间
        """
        from entities import SchedulingWafer

        # 创建临时wafer对象（不添加到system）
        temp_wafer = SchedulingWafer(wafer_base)
        temp_wafer.current_location_id = temp_wafer.foup_location_id

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
        adjusted_current = wafer.current_step_index - wafer._temp_park_count

        for i in range(adjusted_current + 1, len(sequence.sequence_steps)):
            step = sequence.sequence_steps[i]
            if step.target_modules:
                process_type = ConfigService.get_process_type(
                    module_id_to_str(step.target_modules[0])
                )
                remaining.append({'step': step, 'process_type': process_type})

        return remaining

    def _plan_path_with_timing(
            self,
            wafer: SchedulingWafer,
            remaining_steps: List[Dict],
            start_time: float,
            start_location: str = None,
            can_delay_start: bool = True) -> Tuple[List[PathStep], float]:
        """Plan a remaining route from a controllable execution boundary."""
        plan_start_time = start_time
        relative_time = 0.0
        current_location = start_location or wafer.foup_location_id
        temp_path = []

        for step_info in remaining_steps:
            process_type = step_info['process_type']
            step = step_info['step']

            # 选择位置
            location_id = self._select_location_for_step(wafer, step, process_type)
            
            if not location_id and process_type not in ['TBS', 'LL']:
                logging.error(f"无法为工艺 {process_type} 选择位置")
                return [], 0.0

            # 检查Chamber约束。安全起点可以整体推迟；不可延迟的执行前缀
            # 则在当前 source 等到目标 chamber 可接收后再启动搬运。
            is_target_chamber = ConfigService.is_chamber_module(
                LocationParser.get_base_module_id(location_id) if location_id else ''
            )
            if is_target_chamber:
                if can_delay_start:
                    plan_start_time = self._adjust_leave_time_for_chamber(
                        location_id,
                        plan_start_time,
                        relative_time,
                    )
                else:
                    chamber = self.system.chambers.get(location_id)
                    if chamber:
                        source_ready_time = plan_start_time + relative_time
                        chamber_ready_time = self._get_chamber_ready_time(chamber)
                        required_wait = max(0.0, chamber_ready_time - source_ready_time)
                        if required_wait > 0:
                            self._move_relative_wait_to_safe_hold(
                                temp_path, required_wait
                            )
                            relative_time += required_wait

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
        return self._convert_to_path_steps(temp_path, plan_start_time), plan_start_time

    def _move_relative_wait_to_safe_hold(self, temp_path: List[Dict], wait_time: float):
        """Move a future chamber wait to the latest planned safe hold if possible."""
        if not temp_path:
            return

        hold_idx = None
        for idx in range(len(temp_path) - 1, -1, -1):
            item = temp_path[idx]
            if self._is_safe_hold(item['process_type'], item['location_id']):
                hold_idx = idx
                break

        if hold_idx is None:
            temp_path[-1]['relative_departure'] += wait_time
            return

        temp_path[hold_idx]['relative_departure'] += wait_time
        for idx in range(hold_idx + 1, len(temp_path)):
            temp_path[idx]['relative_arrival'] += wait_time
            temp_path[idx]['relative_departure'] += wait_time

    def _select_location_for_step(self, wafer: SchedulingWafer, step, process_type: str) -> Optional[str]:
        """为工艺步骤选择位置"""
        if process_type in ['TBS', 'LL']:
            return ''  # 动态选择
        elif process_type == 'FOUP':
            return wafer.foup_location_id
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
        """查找最佳Chamber（最早可用）。排除离线和故障 chamber。"""
        candidate_chambers = [
            c for c in self.system.chambers.values()
            if any(LocationParser.get_base_module_id(c.location_id) == module_id_to_str(mid)
                   for mid in target_modules)
            and c.base.online == 1
            and not c.is_faulted
        ]

        if not candidate_chambers:
            return None

        # 选择最早就绪的Chamber
        best_chamber = min(candidate_chambers, 
                          key=lambda c: self._get_chamber_ready_time(c))
        return best_chamber.location_id

    def _find_best_chamber_for_type(self, process_type: str) -> Optional[str]:
        """按 process_type 查找最佳可用 chamber（排除离线和故障 chamber）。"""
        module_ids = ConfigService.get_modules_by_process_type(process_type)
        candidate_chambers = [
            c for c in self.system.chambers.values()
            if LocationParser.get_base_module_id(c.location_id) in module_ids
            and c.base.online == 1
            and not c.is_faulted
        ]
        if not candidate_chambers:
            return None
        return min(candidate_chambers, key=lambda c: self._get_chamber_ready_time(c)).location_id

    def temp_park_wafer(self, wafer: SchedulingWafer, insert_step_idx: int) -> bool:
        """在 path_plan[insert_step_idx] 之前插入 TEMP_PARK 步骤，把 wafer 搬到空闲 LL/TBS。

        统一两种触发场景:
        1. 暂存超时: insert_step_idx = wafer.current_step_index
           wafer 在 chamber 加工完毕后长时间无法离开，临时搬离腾出 chamber。
        2. 同工艺全故障: insert_step_idx = 故障 chamber 在 path_plan 中的索引
           找不到替代 chamber，先在附近停靠等待 unfault。
        """
        # zone 推断：
        # 暂存超时 → wafer 当前仍在 chamber，用 current_location_id
        # 全故障   → path_plan[insert_step_idx] 是那个故障 chamber，用其 location
        if insert_step_idx == wafer.current_step_index:
            base = LocationParser.get_base_module_id(wafer.current_location_id)
        else:
            blocked_location = wafer.path_plan[insert_step_idx].location_id or ''
            base = LocationParser.get_base_module_id(blocked_location)
        zone = ConfigService.get_module_zone(base)

        park_location = self._find_park_location(zone)
        if park_location is None:
            logging.warning(
                f"{LogIcon.WARNING} Wafer {wafer.wafer_id} 找不到 TEMP_PARK 槽位 "
                f"({zone} zone)，wafer 原地等待"
            )
            return False

        # 立即预订槽位，防止其他 wafer 抢占
        park_entity = self.system.get_location_by_id(park_location)
        if park_entity:
            park_entity.booked_wafer_id = wafer.wafer_id

        # park task 的出发位置
        if insert_step_idx > 0:
            prev_location = wafer.path_plan[insert_step_idx - 1].location_id or wafer.current_location_id
        else:
            prev_location = wafer.current_location_id

        # 插入 TEMP_PARK PathStep（时间字段仅作占位，实际调度以 wall-clock 为准）
        park_step = PathStep(
            process_type='TEMP_PARK',
            location_id=park_location,
            estimated_arrival=time.time(),
            estimated_departure=time.time(),
        )
        wafer.path_plan.insert(insert_step_idx, park_step)

        # 在 assignment_queue 对应位置插入去停靠位的 transport task
        queue_idx = insert_step_idx - wafer.current_step_index
        park_task = TransportTask(
            wafer_id=wafer.wafer_id,
            from_location=prev_location,
            to_location=park_location,
        )
        wafer.assignment_queue.insert(queue_idx, park_task)

        # 更新紧后任务的 from_location（wafer 将从 park_location 出发）
        if queue_idx + 1 < len(wafer.assignment_queue):
            wafer.assignment_queue[queue_idx + 1].from_location = park_location

        trace_writer.emit('wafer_temp_parked', wafer_id=wafer.wafer_id, park_location=park_location)
        if self.metrics:
            self.metrics.on_temp_parked(wafer)
        logging.info(
            f"{LogIcon.PLAN} TEMP_PARK: Wafer {wafer.wafer_id} → {park_location} ({zone} zone)"
        )
        return True

    def _find_park_location(self, zone: str) -> Optional[str]:
        """根据 zone 查找空闲的 TEMP_PARK 槽位。
        TMA zone → LL_A (in-type)；TMB zone → 优先冷却 TBS，备选普通 TBS。
        """
        if zone == 'TMA':
            for ll_id, ll in sorted(self.system.lls.items()):
                base = LocationParser.get_base_module_id(ll_id)
                if (ConfigService.is_ll_in_slot(base)
                        and ll.busy == 0
                        and ll.booked_wafer_id is None):
                    return ll_id
            return None

        if zone == 'TMB':
            # 优先普通 TBS（不会触发冷却工艺）
            for tbs_id, tbs in sorted(self.system.tbs_locations.items()):
                if not tbs.has_cooling and tbs.busy == 0 and tbs.booked_wafer_id is None:
                    return tbs_id
            # 备选冷却 TBS
            for tbs_id, tbs in sorted(self.system.tbs_locations.items()):
                if tbs.has_cooling and tbs.busy == 0 and tbs.booked_wafer_id is None:
                    return tbs_id
            return None

        return None

    def _get_chamber_ready_time(self, chamber: SchedulingChamber) -> float:
        """计算Chamber何时就绪"""
        current_time = time.time()

        # 空闲且无队列
        if chamber.busy == 0 and not chamber.task_queue and not chamber.current_task:
            return current_time

        # 当前任务剩余时间
        ready_time = current_time
        if chamber.current_task and chamber.task_start_time:
            remaining = chamber.current_task.duration - (current_time - chamber.task_start_time)
            ready_time += max(remaining, 0)

        # 队列任务：若预订时记录了 estimated_arrival，则 chamber 必须等到
        # wafer 实际到达后才能开始，空闲等待区间不计入占用时间。
        for task in chamber.task_queue:
            if task.task_type in ('wafer_process', 'macro'):
                task_start = max(ready_time, task.estimated_arrival) if task.estimated_arrival > 0 else ready_time
                ready_time = task_start + task.duration

        return ready_time

    def _adjust_leave_time_for_chamber(
            self,
            chamber_id: str,
            leave_foup_time: float,
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

    def _estimate_current_arrival(self, wafer: SchedulingWafer, target_location: str) -> float:
        """实时估算 wafer 到达 target_location 的时间。

        比 path_plan 中的 estimated_arrival 更准确：根据 wafer 当前实际状态推算，
        不受故障改道或 TEMP_PARK 导致的过期时间戳影响。
        """
        current_time = time.time()

        # 若 wafer 仍在某 chamber 加工，需等剩余加工时间
        current_chamber = self.system.chambers.get(wafer.current_location_id)
        if (current_chamber and current_chamber.current_task and
                current_chamber.current_task.wafer_id == wafer.wafer_id and
                current_chamber.task_start_time):
            remaining = current_chamber.current_task.duration - (
                current_time - current_chamber.task_start_time
            )
            from_time = current_time + max(remaining, 0)
        else:
            # wafer 在 LL/TBS/TEMP_PARK 等待，或刚完成加工等待传输
            from_time = current_time

        return from_time + self._calc_transport_time(wafer.current_location_id, target_location)

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
        if process_type == 'TEMP_PARK':
            return 0

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
                duration=process_time,
                estimated_arrival=step.estimated_arrival,
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
