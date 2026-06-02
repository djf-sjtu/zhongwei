# -*- coding: utf-8 -*-
# services/execution/transport_executor.py
"""
传输执行器 (CVD平台版本)
职责：机械臂传输任务的执行与到达事件处理
支持批处理wafer的传输
"""
import time
import threading
import logging
from typing import Optional, Callable

from entities import SchedulingRobot, SchedulingWafer, SchedulingLL, TransportTask
from entities.batch_wafer import is_batch_wafer  # ★ CVD: 批处理支持
from config import SystemConfig, ConfigService
from utils import LocationParser, is_ll, is_foup, is_chamber, is_aligner, is_tbs, calc_transport_time
from domain import ResourceValidator
from common import LogIcon, WaferPriorityCalculator
from services.scheduling.robot_selector import RobotSelector
class TransportExecutor:
    """传输执行器 - 负责机械臂传输的执行"""

    def __init__(self, system, scheduler_core, path_planner):
        self.system = system
        self.scheduler = scheduler_core
        self.task_executor = None  # 后续注入
        self.path_planner = path_planner
        self.priority_calc = WaferPriorityCalculator()

        # 回调
        self.on_wafer_completed: Optional[Callable] = None
        self.on_wafer_loaded_callback = None

    # ================================================================
    # 机械臂队列处理
    # ================================================================

    def process_robot_queue(self, robot: SchedulingRobot):
        """处理机械臂传输队列"""
        if not robot.is_robot_available() or not robot.transport_queue:
            return

        # 找到所有ready的任务
        ready_tasks = self._find_ready_tasks(robot)

        if not ready_tasks:
            # 没有ready的任务，第一个任务加入等待
            self._handle_not_ready(robot)
            return

        # 按优先级排序，选择最高优先级的任务
        best_task = self._select_best_task(ready_tasks, robot)

        # 预订目标位置并执行传输
        self._book_location(best_task.to_location, best_task.wafer_id)
        self._execute_transport(robot, best_task)

    def _find_ready_tasks(self, robot: SchedulingRobot) -> list:
        """查找ready的传输任务"""
        ready_tasks = []

        for i, task in enumerate(robot.transport_queue):
            wafer = self.system.wafers.get(task.wafer_id)
            if not wafer or not wafer.assignment_queue:
                continue

            next_assignment = wafer.assignment_queue[0]

            # 检查wafer是否ready且不busy
            if (ResourceValidator.is_next_step_ready(wafer, next_assignment, self.system)
                    and wafer.busy == 0):
                priority = self.priority_calc.calculate_priority(wafer)
                ready_tasks.append((i, task, wafer, priority))

        return ready_tasks

    def _handle_not_ready(self, robot: SchedulingRobot):
        """处理队列中没有ready任务的情况"""
        task = robot.transport_queue.pop(0)
        wafer = self.system.wafers.get(task.wafer_id)

        if wafer:
            self.scheduler.add_to_waiting(wafer)

        # 递归处理下一个任务
        self.process_robot_queue(robot)

    def _select_best_task(self, ready_tasks: list, robot: SchedulingRobot) -> TransportTask:
        """从ready任务中选择最佳任务（最高优先级）"""
        ready_tasks.sort(key=lambda x: x[3], reverse=True)
        idx, task, wafer, priority = ready_tasks[0]

        logging.info(f"{LogIcon.PLAN} {robot.robot_id} 选择 Wafer{wafer.wafer_id} "
                     f"(优先级={priority:.3f}, 队列{len(robot.transport_queue)}个任务)")

        robot.transport_queue.pop(idx)
        return task

    def _book_location(self, location_id: str, wafer_id: int):
        """预订目标位置"""
        location = self.system.get_location_by_id(location_id)
        if location and (is_ll(location_id) or is_tbs(location_id) or is_aligner(location_id)):
            location.booked_wafer_id = wafer_id

    # ================================================================
    # 传输执行
    # ================================================================

    def _execute_transport(self, robot: SchedulingRobot, task: TransportTask):
        """执行传输任务 (支持批处理)"""
        # ✅ Switch检测
        switch_wafer = self._find_switch_candidate(robot.robot_id, task)
        if switch_wafer:
            self.system.waiting_wafers.remove(switch_wafer)
            self._execute_switch(robot, task, switch_wafer)
            return

        def transport_thread():
            try:
                wafer = self.system.wafers[task.wafer_id]
                wafer.busy = 1
                transport_time = SystemConfig.TRANSPORT_BASE_TIME

                # ★ CVD: 判断是否是批处理
                is_batch = is_batch_wafer(wafer.base)

                # 1. 机械臂移动到from location
                #time.sleep(transport_time)

                # 2. 抓取
                if is_batch:
                    self._pickup_batch(robot, task, wafer, 1)
                else:
                    self._pickup(robot, task, wafer,1)

                # 3. 机械臂移动到target location
                time.sleep(transport_time)

                # 4. 放置 - ★ CVD: 根据是否batch调用不同方法
                if is_batch:
                    self._dropoff_batch(robot, task, wafer,1)
                else:
                    self._dropoff(robot, task, wafer,1)

            except Exception as e:
                logging.error(f"{LogIcon.ERROR} 传输错误: {e}")
                self._clear_booking(task.to_location)
            finally:
                robot.busy = 0
                self.process_robot_queue(robot)

        robot.busy = 1
        threading.Thread(target=transport_thread, daemon=True).start()

    # ================================================================
    # swicth
    # ================================================================

    def _find_switch_candidate(self, robot_id: str, task: TransportTask) -> Optional[SchedulingWafer]:
        """查找Switch候选"""
        for wafer in self.system.waiting_wafers:
            if not wafer.assignment_queue:
                continue
            task2 = wafer.assignment_queue[0]
            if (task2.to_location == task.from_location and
                    RobotSelector.select_robot(task2.from_location, task2.to_location) == robot_id):
                return wafer
        return None

    def _execute_switch(self, robot: SchedulingRobot, task1: TransportTask, wafer2: SchedulingWafer):
        """执行Switch操作（支持batch）"""
        logging.info(f'{LogIcon.PLAN} 开始SWITCH: Wafer{task1.wafer_id} <-> Wafer{wafer2.wafer_id}')
        def switch_thread():
            try:
                wafer1 = self.system.wafers[task1.wafer_id]
                task2 = wafer2.assignment_queue[0]

                wafer1.busy = 1
                wafer2.busy = 1

                is_batch1 = is_batch_wafer(wafer1.base)
                is_batch2 = is_batch_wafer(wafer2.base)

                # Step 1: 下臂取wafer2
                if is_batch2:
                    self._pickup_batch(robot, task2, wafer2,2)
                else:
                    self._pickup(robot, task2, wafer2,2)

                # Step 2: 移动到A
                transport_time = SystemConfig.TRANSPORT_BASE_TIME
                time.sleep(transport_time)

                # Step 3: 上臂取wafer1
                # 创建临时任务用于pickup
                if is_batch1:
                    self._pickup_batch(robot, task1, wafer1,1)
                else:
                    self._pickup(robot, task1, wafer1,1)

                # Step 4: 下臂放wafer2到A（使用dropoff）
                if is_batch2:
                    self._dropoff_batch(robot, task2, wafer2,2)
                else:
                    self._dropoff(robot, task2, wafer2,2)

                # Step 5: 上臂移动并放wafer1到B（先移动再dropoff）
                transport_time = SystemConfig.TRANSPORT_BASE_TIME
                time.sleep(transport_time)
                if is_batch1:
                    self._dropoff_batch(robot, task1, wafer1,1)
                else:
                    self._dropoff(robot, task1, wafer1,1)

                logging.info(f"{LogIcon.COMPLETE} Switch完成")

            finally:
                robot.busy = 0
                self.process_robot_queue(robot)

        robot.busy = 1
        threading.Thread(target=switch_thread, daemon=True).start()

    # ================================================================
    # 机械臂操作、资源管理
    # ================================================================
    def _pickup(self, robot: SchedulingRobot, task: TransportTask, wafer: SchedulingWafer, arm_id: int):
        """执行抓取"""
        logging.info(
            f"1451 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})"
            f"(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"will move to {robot.robot_id} Slot {arm_id}")

        self._move_Z(robot, task.from_location)
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        logging.info(
            f"63 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})"
            f"(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"picked by {robot.robot_id} Slot {arm_id}")

        # 释放源位置
        self._release_source(task.from_location)

    def _dropoff(self, robot: SchedulingRobot, task: TransportTask, wafer: SchedulingWafer, arm_id: int):
        """执行放置（集成完整状态更新）"""
        logging.info(
            f"1451 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})"
            f"(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"will move from {robot.robot_id} Slot {arm_id}")

        self._move_Z(robot, task.to_location)
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        logging.info(
            f"63 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})"
            f"(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"placed on {task.to_location}")

        # ✅ 集成状态更新
        wafer.assignment_queue.pop(0)
        wafer.move_to_next_location(task.to_location)

        target = self.system.get_location_by_id(task.to_location)
        target.current_wafer_id = wafer.wafer_id
        target.busy = 1

        self._handle_arrival(wafer, target)

    def _pickup_batch(self, robot: SchedulingRobot, task: TransportTask, wafer, arm_id: int):
        """执行批处理抓取 - 同时抓取左右两片wafer"""
        batch_wafer = wafer.base
        left_wafer = batch_wafer.left_wafer
        right_wafer = batch_wafer.right_wafer
        # 日志: 左片
        logging.info(
            f"1451 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{left_wafer.wafer_id})"
            f"(#{left_wafer.source_foup.value.replace('_', ' ')} {left_wafer.source_foup_slot.value}) "
            f"will move to {robot.robot_id} Slot {arm_id} Left")

        # 日志: 右片
        logging.info(
            f"1451 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{right_wafer.wafer_id})"
            f"(#{right_wafer.source_foup.value.replace('_', ' ')} {right_wafer.source_foup_slot.value}) "
            f"will move to {robot.robot_id} Slot {arm_id} Right")

        # 移动机械臂
        self._move_Z(robot, task.from_location)

        # Pick时间
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        # 日志: 左片picked
        logging.info(
            f"63 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{left_wafer.wafer_id})"
            f"(#{left_wafer.source_foup.value.replace('_', ' ')} {left_wafer.source_foup_slot.value}) "
            f"picked by {robot.robot_id} Slot {arm_id} Left")

        # 日志: 右片picked
        logging.info(
            f"63 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{right_wafer.wafer_id})"
            f"(#{right_wafer.source_foup.value.replace('_', ' ')} {right_wafer.source_foup_slot.value}) "
            f"picked by {robot.robot_id} Slot {arm_id} Right")

        # 释放源位置
        self._release_source(task.from_location)

    def _dropoff_batch(self, robot: SchedulingRobot, task: TransportTask, wafer, arm_id: int):
        """执行批处理放置（集成完整状态更新）"""
        batch_wafer = wafer.base
        left_wafer = batch_wafer.left_wafer
        right_wafer = batch_wafer.right_wafer
        # 日志: 左片
        logging.info(
            f"1451 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{left_wafer.wafer_id})"
            f"(#{left_wafer.source_foup.value.replace('_', ' ')} {left_wafer.source_foup_slot.value}) "
            f"will move from {robot.robot_id} Slot {arm_id} Left")

        # 日志: 右片
        logging.info(
            f"1451 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{right_wafer.wafer_id})"
            f"(#{right_wafer.source_foup.value.replace('_', ' ')} {right_wafer.source_foup_slot.value}) "
            f"will move from {robot.robot_id} Slot {arm_id} Right")

        # 移动Z轴
        self._move_Z(robot, task.to_location)

        # Place时间 (双叉稍慢)
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        # 日志: 左片placed
        logging.info(
            f"63 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{left_wafer.wafer_id})"
            f"(#{left_wafer.source_foup.value.replace('_', ' ')} {left_wafer.source_foup_slot.value}) "
            f"placed on {task.to_location}")

        # 日志: 右片placed
        logging.info(
            f"63 {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{right_wafer.wafer_id})"
            f"(#{right_wafer.source_foup.value.replace('_', ' ')} {right_wafer.source_foup_slot.value}) "
            f"placed on {task.to_location}")

        # ✅ 集成状态更新
        wafer.assignment_queue.pop(0)
        wafer.move_to_next_location(task.to_location)

        target = self.system.get_location_by_id(task.to_location)
        target.current_wafer_id = wafer.wafer_id
        target.busy = 1

        self._handle_arrival(wafer, target)

    def _move_Z(self, robot: SchedulingRobot, location_id: str):
        """移动机械臂到指定位置（处理Z轴）"""
        target_z = LocationParser.get_z_level_for_location(location_id)

        if robot.current_z_level != target_z:
            robot.is_z_moving = True
            time.sleep(SystemConfig.Z_MOVE_TIME)
            robot.current_z_level = target_z
            robot.is_z_moving = False

    def _release_source(self, location_id: str):
        """释放源位置"""
        location = self.system.get_location_by_id(location_id)

        if not location:
            return

        # 释放基本状态
        location.busy = 0
        location.current_wafer_id = None

        # TBS/Aligner/LL需要额外释放预订状态
        if is_tbs(location_id) or is_aligner(location_id) or is_ll(location_id):
            location.booked_wafer_id = None

        # Chamber - 处理任务队列
        if is_chamber(location_id):
            if self.task_executor:
                self.task_executor.process_chamber_queue(location)

        # FOUP - 触发装载下一片wafer（关键！）
        if is_foup(location_id):
            if self.on_wafer_loaded_callback:
                self.on_wafer_loaded_callback()

    def _clear_booking(self, location_id: str):
        """清除位置预订（错误处理）"""
        location = self.system.get_location_by_id(location_id)
        if location and hasattr(location, 'booked_wafer_id'):
            location.booked_wafer_id = None

    # ================================================================
    # 到达处理
    # ================================================================

    def _handle_arrival(self, wafer: SchedulingWafer, location):
        """处理到达事件 - 分发到不同处理器"""
        location_id = location.location_id

        if is_foup(location_id):
            self._arrival_foup(wafer, location)
        elif is_chamber(location_id):
            self._arrival_chamber(wafer, location)
        elif is_aligner(location_id):
            self._arrival_aligner(wafer, location)
        elif is_ll(location_id):
            self._arrival_ll(wafer, location)
        elif is_tbs(location_id):
            self._arrival_tbs(wafer, location)

    def _arrival_foup(self, wafer: SchedulingWafer, foup):
        """FOUP到达"""
        foup.busy = 0
        wafer.busy = 0

        if wafer.is_sequence_completed():
            logging.info(f"{LogIcon.COMPLETE} Wafer {wafer.wafer_id} 完成全部工艺")
            if self.on_wafer_completed:
                self.on_wafer_completed(wafer)
        else:
            self.scheduler.assign_next_task(wafer)

    def _arrival_chamber(self, wafer: SchedulingWafer, chamber):
        """Chamber到达"""
        current_time = time.time()
        current_step = wafer.path_plan[wafer.current_step_index - 1]
        delay = current_time - current_step.estimated_arrival

        if delay > 2:
            self.path_planner.update_path_if_delayed(wafer, delay)

        self.task_executor.start_chamber_task(chamber, wafer)

    def _arrival_aligner(self, wafer: SchedulingWafer, aligner):
        """Aligner到达"""
        logging.info(f"{LogIcon.TRANSPORT} Wafer {wafer.wafer_id} 到达 {aligner.location_id}")

        # 启动Aligner任务
        self.task_executor.start_aligner_task(aligner, wafer)

    def _arrival_ll(self, wafer: SchedulingWafer, ll: SchedulingLL):
        """LL到达"""
        logging.info(f"{LogIcon.TRANSPORT} Wafer {wafer.wafer_id} 到达 {ll.location_id}")

        base_module = LocationParser.get_base_module_id(ll.location_id)

        # LL进片槽 - 需要抽真空
        if ConfigService.is_ll_in_slot(base_module):
            self._handle_ll_in_arrival(ll, wafer)
        # LL出片槽 - 需要破真空
        elif ConfigService.is_ll_out_slot(base_module):
            self._handle_ll_out_arrival(ll, wafer)
        else:
            # 其他情况直接分配下一任务
            wafer.busy = 0
            self.scheduler.assign_next_task(wafer)

    def _handle_ll_in_arrival(self, ll: SchedulingLL, wafer: SchedulingWafer):
        """处理LL进片槽到达 - 抽真空"""
        # 检查Dry Pump是否忙碌
        if self.system.dry_pump_busy == 1:
            # logging.info(f"{LogIcon.WAIT} Wafer {wafer.wafer_id}: Dry Pump正忙，等待抽真空")
            threading.Timer(1.0, lambda: self._handle_ll_in_arrival(ll, wafer)).start()
            return

        pump_duration = SystemConfig.DRY_PUMP_CONFIG['pump_time_seconds']

        # 设置Dry Pump和LL组状态
        self.system.dry_pump_busy = 1
        self._set_ll_group_pumping(ll.location_id, True)
        ll_log_name = self._get_real_ll(ll.location_id)
        log_char = ll_log_name.split('_')[-1]
        pumpBegin_log_id = '242' if log_char=='A' else '243'
        logging.info(f"{pumpBegin_log_id} LL {log_char} LL INFO LoadLock LoadLock {log_char} begin pumping...")

        def pump_complete():
            self.system.dry_pump_busy = 0
            self._set_ll_group_pumping(ll.location_id, False)
            pumpEnd_log_id = '246' if log_char == 'A' else '247'
            logging.info(f"{pumpEnd_log_id} LL {log_char} LL INFO LoadLock LoadLock {log_char} pumping end, spending time is {pump_duration}s")

            # 计算延迟 - 使用departure时间
            current_step = wafer.path_plan[wafer.current_step_index - 1]
            delay = time.time() - current_step.estimated_departure
            wait_time = max(0, -delay)

            self._log_deviation(wafer, ll.location_id, delay)

            def leave_ll():
                wafer.busy = 0
                self.scheduler.assign_next_task(wafer)

            if wait_time > 0:
                # 早到，需要等待
                threading.Timer(wait_time, leave_ll).start()
            else:
                # 准时或晚到，直接离开
                leave_ll()

        threading.Timer(pump_duration, pump_complete).start()

    def _handle_ll_out_arrival(self, ll: SchedulingLL, wafer: SchedulingWafer):
        """处理LL出片槽到达 - 破真空"""
        # 检查Dry Pump是否忙碌
        if self.system.dry_pump_busy == 1:
            logging.info(f"{LogIcon.WAIT} Wafer {wafer.wafer_id}: Dry Pump正忙，等待通气")
            threading.Timer(1.0, lambda: self._handle_ll_out_arrival(ll, wafer)).start()
            return

        vent_duration = SystemConfig.DRY_PUMP_CONFIG['vent_time_seconds']

        # 设置Dry Pump和LL组状态
        self.system.dry_pump_busy = 1
        self._set_ll_group_pumping(ll.location_id, True)
        ll_log_name = self._get_real_ll(ll.location_id)
        log_char = ll_log_name.split('_')[-1]
        ventBegin_log_id = '240' if log_char=='A' else '241'
        logging.info(f"{ventBegin_log_id} LL {log_char} LL INFO LoadLock LoadLock {log_char} begin venting...")

        def vent_complete():
            self.system.dry_pump_busy = 0
            self._set_ll_group_pumping(ll.location_id, False)
            ventEnd_log_id = '244' if log_char=='A' else '245'
            logging.info(f"{ventEnd_log_id} LL {log_char} LL INFO LoadLock LoadLock {log_char} venting end, spending time is {vent_duration}s")

            ll.busy = 0
            ll.current_wafer_id = None
            wafer.busy = 0
            self.scheduler.assign_next_task(wafer)

        threading.Timer(vent_duration, vent_complete).start()

    def _set_ll_group_pumping(self, ll_location_id: str, is_pumping: bool):
        """设置LL组的pumping状态"""
        ll_group = self._get_ll_group(ll_location_id)
        for ll in ll_group:
            ll.is_pumping = is_pumping

    def _get_real_ll(self, ll_module: str) -> str:
        """
        获取真实的LL名称

        LL_A和LL_C返回LL_A（LL_C是LL_A的下层）
        LL_B和LL_D返回LL_B（LL_D是LL_B的下层）

        Args:
            ll_module: LL的ll_module

        Returns:
            str: 真实的LL名称（LL_A 或 LL_B）
        """
        if 'LL_A' == ll_module or 'LL_C' == ll_module:
            return 'LL_A'
        elif 'LL_B' == ll_module or 'LL_D' == ll_module:
            return 'LL_B'
        return ll_module  # 兜底

    def _get_ll_group(self, ll_location_id: str) -> list:
        base_module = LocationParser.get_base_module_id(ll_location_id)
        groupA = []
        groupB = []
        for ll_id, ll in self.system.lls.items():
            if ll_id in ['LL_A','LL_C']:
                groupA.append(ll)
            else:
                groupB.append(ll)

        return groupA if base_module in groupA else groupB

    def _arrival_tbs(self, wafer: SchedulingWafer, tbs):
        """TBS到达"""
        def leave_tbs():
            wafer.busy = 0
            next_task = wafer.assignment_queue[0] if wafer.assignment_queue else None
            if next_task and next_task.to_location and is_chamber(next_task.to_location):
                target = self.system.chambers.get(next_task.to_location)
                if target and (target.busy != 0 or target.is_faulted or target.current_wafer_id is not None):
                    self.scheduler.add_to_waiting(wafer)
                    return
            self.scheduler.assign_next_task(wafer)

        logging.info(f"{LogIcon.TRANSPORT} Wafer {wafer.wafer_id} 到达 {tbs.location_id}")

        # 检查下一步骤是否是LL
        if wafer.current_step_index < len(wafer.path_plan):
            next_step = wafer.path_plan[wafer.current_step_index]
            if next_step.process_type == 'LL':
                leave_tbs()
                return

        # 否则，计算延迟并等待
        current_step = wafer.path_plan[wafer.current_step_index - 1]
        delay = time.time() - current_step.estimated_arrival
        wait_time = max(0.0, 0.0 - delay)
        self._log_deviation(wafer, tbs.location_id, delay)

        if wait_time > 0:
            # 早到，需要等待
            threading.Timer(wait_time, leave_tbs).start()
        else:
            # 准时或晚到，直接离开
            leave_tbs()

    def _log_deviation(self, wafer: SchedulingWafer, location_id: str, delay: float):
        """记录调度偏差"""
        if delay >= 0:
            logging.info(f"{LogIcon.PLAN} Wafer {wafer.wafer_id} 在 {location_id} 延后 {delay:.1f}s")
        else:
            logging.info(f"{LogIcon.WAIT} Wafer {wafer.wafer_id} 在 {location_id} 提前 {-delay:.1f}s")