# -*- coding: utf-8 -*-
# services/scheduling/scheduler_core.py - 核心调度服务
"""
核心调度服务
职责：任务分配、等待队列管理
"""
import time
import logging

from entities import SchedulingWafer
from config import SystemConfig
from domain import ResourceValidator
from common import LogIcon
from .robot_selector import RobotSelector
from utils import is_chamber
from services.planning import PathPlanner

class SchedulerCore:
    """核心调度器"""

    def __init__(self, system, resource_selector):
        self.system = system
        self.resource_selector = resource_selector
        self.transport_service = None  # 后续注入

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
        robot_id = RobotSelector.select_robot(next_task.from_location, next_task.to_location)
        robot = self.system.robots.get(robot_id)

        if robot:
            robot.transport_queue.append(next_task)
            logging.info(f"{LogIcon.PLAN} 分配{robot.robot_id}传输任务: Wafer {wafer.wafer_id}: "
                        f"{next_task.from_location} → {next_task.to_location}")

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
        """检查chamber暂存时间"""
        if not wafer.storage_start_time:
            return

        storage_duration = time.time() - wafer.storage_start_time
        warning_threshold = SystemConfig.SCHEDULING_CONFIG.get(
            'chamber_storage_warning_threshold', 120.0
        )

        if storage_duration > warning_threshold:
            logging.warning(f"⚠️ Wafer {wafer.wafer_id} 在{wafer.current_location_id} 暂存时间过长: {storage_duration:.1f}s")

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
