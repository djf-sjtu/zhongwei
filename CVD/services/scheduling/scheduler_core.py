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
from utils import is_chamber, calc_transport_time
from services.planning import PathPlanner

class SchedulerCore:
    """核心调度器"""

    def __init__(self, system, resource_selector):
        self.system = system
        self.resource_selector = resource_selector
        self.transport_service = None  # 后续注入
        self.path_planner = None       # 后续注入
        self._last_warning_times: dict = {}

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
                    self._check_storage_time(wafer)
                    continue

            # 检查目标是否ready
            if ResourceValidator.is_next_step_ready(wafer, next_assignment, self.system):
                self.system.waiting_wafers.remove(wafer)
                self.assign_next_task(wafer)
            else:
                # 检查暂存时间
                self._check_storage_time(wafer)

    def _check_storage_time(self, wafer: SchedulingWafer):
        """检查 chamber 暂存时间，超时触发临时停靠"""
        if not wafer.storage_start_time:
            return

        # 只对停留在 process chamber（NUC/BULK）中的 wafer 触发 TEMP_PARK
        if not is_chamber(wafer.current_location_id):
            return

        storage_duration = time.time() - wafer.storage_start_time
        threshold = SystemConfig.SCHEDULING_CONFIG.get('chamber_storage_warning_threshold', 120.0)

        if storage_duration <= threshold:
            return

        # 下一目标 chamber 即将腾出，继续等待
        if self._next_chamber_frees_soon(wafer, threshold):
            return

        # 超时：尝试临时停靠
        if self.path_planner and self.path_planner.temp_park_wafer(wafer, wafer.current_step_index):
            wafer.storage_start_time = None
            self.system.waiting_wafers.remove(wafer)
            self.assign_next_task(wafer)
        else:
            now = time.time()
            key = str(wafer.wafer_id)
            if now - self._last_warning_times.get(key, 0) >= 10.0:
                logging.warning(
                    f"⚠️ Wafer {wafer.wafer_id} 在 {wafer.current_location_id} "
                    f"暂存超时 {storage_duration:.1f}s，无可用停靠位"
                )
                self._last_warning_times[key] = now

    def _next_chamber_frees_soon(self, wafer: SchedulingWafer, threshold: float) -> bool:
        """判断下一目标 chamber 是否即将腾出，避免不必要的 TEMP_PARK"""
        next_task = wafer.assignment_queue[0] if wafer.assignment_queue else None
        if not next_task or not next_task.to_location:
            return False
        if not is_chamber(next_task.to_location):
            return False

        target = self.system.chambers.get(next_task.to_location)
        if not target:
            return False
        if target.is_faulted:
            return False
        if not target.current_task:
            return True  # 已空闲，马上能接收

        # 当前任务剩余时间 < threshold 则视为"即将腾出"
        if target.task_start_time:
            elapsed = time.time() - target.task_start_time
            remaining = target.current_task.duration - elapsed
            if remaining < threshold:
                return True

        return False
