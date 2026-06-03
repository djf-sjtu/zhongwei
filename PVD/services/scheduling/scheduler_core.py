# -*- coding: utf-8 -*-
# services/scheduling/scheduler_core.py - 核心调度服务
"""
核心调度服务
职责：任务分配、等待队列管理
"""
import time
import logging

from entities import SchedulingWafer, SchedulingChamber
from config import SystemConfig, ConfigService
from domain import ResourceValidator
from common import LogIcon, trace_writer
from utils import is_chamber, LocationParser
from services.planning import PathPlanner

class SchedulerCore:
    """核心调度器"""

    def __init__(self, system, resource_selector):
        self.system = system
        self.resource_selector = resource_selector
        self.transport_service = None  # 后续注入
        self.path_planner = None       # 后续注入
        self._last_warning_times: dict = {}  # wafer_id -> 上次打印暂存警告的时间

    def assign_next_task(self, wafer: SchedulingWafer):
        """为wafer分配下一个传输任务到机械臂"""
        if not wafer.assignment_queue:
            return

        # Wafer正忙，加入等待队列
        if wafer.busy == 1:
            logging.warning(f"{LogIcon.PLAN} Wafer {wafer.wafer_id} 正忙，无法分配任务")
            self.add_to_waiting(wafer)
            return

        # 动态选择目标位置（如果需要）
        next_task = wafer.assignment_queue[0]
        if not next_task.to_location:
            location = self.resource_selector.select_next_location_for_wafer(wafer)
            if not location:
                logging.info(f"{LogIcon.WAIT} 动态分配位置失败: Wafer {wafer.wafer_id}: "
                           f"分配传输{next_task.to_location}失败，等待下次分配")
                self.add_to_waiting(wafer)
                return

        # 选择机械臂
        robot_id = ConfigService.get_robot_for_transport(next_task.from_location, next_task.to_location)
        robot = self.system.robots.get(robot_id)

        if robot:
            robot.transport_queue.append(next_task)
            logging.info(f"{LogIcon.PLAN} 分配{robot.robot_id}传输任务: Wafer {wafer.wafer_id}: "
                        f"{next_task.from_location} → {next_task.to_location}")

            # task_assigned trace 事件已下移到 TransportExecutor._select_best_task：
            # 那里是"任务真正被选中执行"的时点，比这里"放进 robot 队列"的语义
            # 更准确——因为 _handle_not_ready 会把暂时不 ready 的任务从 robot 队列
            # pop 掉、wafer 进 waiting_wafers 等下次 check，下次 assign_next_task
            # 再次入队就构成"乐观重试"。重试在 trace 中不应该重复出现。

            # 触发机械臂处理
            if self.transport_service:
                self.transport_service.process_robot_queue(robot)

    def add_to_waiting(self, wafer: SchedulingWafer):
        """添加到等待队列"""
        if wafer not in self.system.waiting_wafers:
            self.system.waiting_wafers.append(wafer)
            logging.info(f"等待: Wafer {wafer.wafer_id} 暂时不能被调度，推入等待队列")

    def check_waiting_wafers(self):
        """检查等待队列"""
        for wafer in list(self.system.waiting_wafers):
            # 无任务，移除
            if not wafer.assignment_queue:
                self.system.waiting_wafers.remove(wafer)
                continue

            next_assignment = wafer.assignment_queue[0]

            # 尝试动态选择位置
            if not next_assignment.to_location:
                location = self.resource_selector.select_next_location_for_wafer(wafer)
                if not location:
                    continue

            # 检查目标是否ready
            if ResourceValidator.is_next_step_ready(wafer, next_assignment, self.system):
                self.system.waiting_wafers.remove(wafer)
                self.assign_next_task(wafer)
            else:
                # 检查暂存时间
                self._check_storage_time(wafer)

    def _check_storage_time(self, wafer: SchedulingWafer):
        """检查chamber暂存时间，超时后触发临时停靠"""
        if not wafer.storage_start_time:
            return
        if not is_chamber(wafer.current_location_id):
            return

        storage_duration = time.time() - wafer.storage_start_time
        threshold = SystemConfig.SCHEDULING_CONFIG.get(
            'chamber_storage_warning_threshold', 120.0
        )

        if storage_duration <= threshold:
            return

        # 下一目标 chamber 即将腾出，不触发临时停靠——继续等待
        if self._next_chamber_frees_soon(wafer, threshold):
            return

        # 超时：尝试临时停靠
        if self.path_planner and self.path_planner.temp_park_wafer(wafer, wafer.current_step_index):
            wafer.storage_start_time = None  # 已处理，清除计时避免重复触发
            self.system.waiting_wafers.remove(wafer)
            self.assign_next_task(wafer)
        else:
            now = time.time()
            if now - self._last_warning_times.get(wafer.wafer_id, 0) >= 10.0:
                logging.warning(
                    f"⚠️ Wafer {wafer.wafer_id} 在 {wafer.current_location_id} "
                    f"暂存超时 {storage_duration:.1f}s，无可用停靠位"
                )
                self._last_warning_times[wafer.wafer_id] = now

    def _next_chamber_frees_soon(self, wafer: SchedulingWafer, threshold: float) -> bool:
        """判断 wafer 的下一目标 chamber 是否即将腾出。

        两种情况均视为"即将腾出"，不应触发临时停靠：
        1. chamber 当前任务剩余时间 < threshold（快完成了，等一等即可）
        2. chamber 任务刚结束（last_task_end_time 在最近数秒内），wafer 尚未被传输走
        """
        next_task = wafer.assignment_queue[0] if wafer.assignment_queue else None
        if not next_task or not next_task.to_location:
            return False
        next_chamber = self.system.chambers.get(next_task.to_location)
        if not next_chamber:
            return False

        now = time.time()
        # 情况1：chamber 正在处理且快完成
        if next_chamber.current_task and next_chamber.task_start_time:
            remaining = next_chamber.current_task.duration - (now - next_chamber.task_start_time)
            if 0 < remaining < threshold:
                return True

        # 情况2：chamber 任务刚结束，transport 尚未腾出（给5s宽限）
        if (not next_chamber.current_task and next_chamber.last_task_end_time and
                now - next_chamber.last_task_end_time < 5.0):
            return True

        return False

    def _steal_work_for_idle_chamber(self, idle_chamber: SchedulingChamber):
        """chamber 空闲时，从同工艺兄弟 chamber 的队列末尾抢一片 wafer。

        触发条件：当前 chamber 无 current_task 且 task_queue 为空，
        而某兄弟 chamber 队列中有 ≥2 个 wafer_process 预订。
        抢末尾那片（等待时间最长，早迁移收益最大）。
        """
        if not self.path_planner:
            return
        module_str = LocationParser.get_base_module_id(idle_chamber.location_id)
        process_type = ConfigService.get_process_type(module_str)
        if not process_type:
            return
        sibling_module_ids = ConfigService.get_modules_by_process_type(process_type)

        # 找积压最多的兄弟 chamber（至少要有 2 个预订才值得抢）
        best_source = None
        max_reserves = 1
        for m in sibling_module_ids:
            loc_id = f"{m}_1"
            if loc_id == idle_chamber.location_id:
                continue
            chamber = self.system.chambers.get(loc_id)
            if not chamber or chamber.is_faulted:
                continue
            reserve_count = sum(
                1 for t in chamber.task_queue if t.task_type == 'wafer_process'
            )
            if reserve_count > max_reserves:
                max_reserves = reserve_count
                best_source = chamber

        if best_source is None:
            return

        # 从队列末尾找一片可抢的 wafer
        # 安全条件：wafer 必须在 waiting_wafers 中静止等待，未被机械臂拿起；
        # 正在传输中的 wafer 不能抢，否则会导致传输时间与实际路径不匹配
        safe_waiting = {id(w) for w in self.system.waiting_wafers}
        for task in reversed(best_source.task_queue):
            if task.task_type != 'wafer_process':
                continue
            wafer = self.system.wafers.get(task.wafer_id)
            if not wafer:
                continue
            if id(wafer) not in safe_waiting:
                continue  # 正在传输中，跳过
            if self.path_planner.replan_for_faulted_chamber(wafer, best_source.location_id):
                logging.info(
                    f"{LogIcon.PLAN} 空闲抢活: Wafer {task.wafer_id} "
                    f"{best_source.location_id} → {idle_chamber.location_id}"
                )
                return

    # ================================================================
    # 级联更新
    # ================================================================

    def _update_affected_wafers_in_chambers(self, wafer: SchedulingWafer):
        """级联更新受影响的wafer"""
        # 记录每个wafer受影响的起始chamber索引
        affected_from = {wafer.wafer_id: wafer.current_step_index}
        to_process = [wafer]

        # 创建PathPlanner实例用于估算传输时间
        path_planner = PathPlanner(self.system)

        while to_process:
            current_wafer = to_process.pop(0)
            start_index = affected_from[current_wafer.wafer_id]

            # 从受影响的位置开始遍历
            for step in current_wafer.path_plan[start_index:]:
                if not is_chamber(step.location_id):
                    continue

                chamber = self.system.chambers.get(step.location_id)
                if not chamber or not chamber.task_queue:
                    continue

                # 找到当前wafer在队列中的位置
                wafer_index = -1
                for i, task in enumerate(chamber.task_queue):
                    if task.wafer_id == current_wafer.wafer_id:
                        wafer_index = i
                        break

                if wafer_index == -1:
                    continue

                prev_departure = step.estimated_departure

                # 更新队列中后续wafer
                for task in chamber.task_queue[wafer_index + 1:]:
                    if task.task_type != 'wafer_process':
                        continue

                    other_wafer = self.system.wafers.get(task.wafer_id)
                    if not other_wafer:
                        continue

                    # 找到other_wafer在该chamber的步骤
                    other_step = None
                    other_step_index = None
                    for idx, s in enumerate(other_wafer.path_plan):
                        if s.location_id == step.location_id:
                            other_step = s
                            other_step_index = idx
                            break

                    if not other_step:
                        continue

                    # 计算传输时间
                    if other_step_index > 0:
                        prev_step = other_wafer.path_plan[other_step_index - 1]
                        transport_time = path_planner._calc_transport_time(
                            prev_step.location_id, step.location_id)
                    else:
                        transport_time = 20.0

                    # 计算新的到达时间
                    new_arrival = max(other_step.estimated_arrival,
                                    prev_departure + transport_time)

                    if new_arrival > other_step.estimated_arrival:
                        time_diff = new_arrival - other_step.estimated_arrival

                        # 从受影响的步骤开始更新
                        for i in range(other_step_index, len(other_wafer.path_plan)):
                            other_wafer.path_plan[i].estimated_arrival += time_diff
                            other_wafer.path_plan[i].estimated_departure += time_diff

                        # 如果other_wafer还没被处理过，加入队列
                        if other_wafer.wafer_id not in affected_from:
                            affected_from[other_wafer.wafer_id] = other_step_index
                            to_process.append(other_wafer)
