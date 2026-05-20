# -*- coding: utf-8 -*-
# services/execution/transport_executor.py
"""
传输执行器
职责：机械臂传输任务的执行与到达事件处理
"""
import time
import threading
import logging
from typing import Optional, Callable

from entities import SchedulingRobot, SchedulingWafer, SchedulingLL, TransportTask
from config import SystemConfig, ConfigService
from utils import LocationParser, is_ll, is_foup, is_chamber, is_aligner, is_tbs
from domain import ResourceValidator
from common import LogIcon, WaferPriorityCalculator


class TransportExecutor:
    """传输执行器 - 负责机械臂传输的执行"""

    def __init__(self, system, scheduler_core):
        self.system = system
        self.scheduler = scheduler_core
        self.task_executor = None  # 后续注入
        self.priority_calc =WaferPriorityCalculator()

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

        logging.info(f"{LogIcon.PLAN} 优先级调度: {robot.robot_id} 选择 Wafer{wafer.wafer_id} "
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
        """执行传输任务"""

        def transport_thread():
            try:
                wafer = self.system.wafers[task.wafer_id]
                wafer.busy = 1

                # 1. 更新路径（如果延迟）
                self._update_path_if_delayed(wafer, task.from_location)

                # 2. 抓取
                self._pickup(robot, task, wafer)

                # 3. 传输
                time.sleep(SystemConfig.TRANSPORT_BASE_TIME)

                # 4. 放置
                self._dropoff(robot, task, wafer)

                # 5. 更新wafer状态
                wafer.assignment_queue.pop(0)
                wafer.move_to_next_location(task.to_location)

                # 6. 更新目标位置
                target = self.system.get_location_by_id(task.to_location)
                target.current_wafer_id = wafer.wafer_id
                target.busy = 1

                # 7. 处理到达
                self._handle_arrival(wafer, target)

            except Exception as e:
                logging.error(f"{LogIcon.ERROR} 传输错误: {e}")
                self._clear_booking(task.to_location)
            finally:
                robot.busy = 0
                self.process_robot_queue(robot)

        robot.busy = 1
        threading.Thread(target=transport_thread, daemon=True).start()

    def _update_path_if_delayed(self, wafer: SchedulingWafer, from_location: str):
        """如果延迟，更新路径规划"""
        base_module = LocationParser.get_base_module_id(from_location)

        if not (ConfigService.is_ll_in_slot(base_module) or is_tbs(from_location)):
            return

        current_step = wafer.path_plan[wafer.current_step_index - 1]
        delay = time.time() - current_step.estimated_departure

        if delay > 2:  # 延迟超过2秒才更新
            logging.info(f"Wafer{wafer.wafer_id} 比计划延迟 {delay:.1f}s, 更新后续path plan")

            # 更新当前wafer的时间
            for i in range(wafer.current_step_index, len(wafer.path_plan)):
                wafer.path_plan[i].estimated_arrival += delay
                wafer.path_plan[i].estimated_departure += delay

            # 级联更新后续wafer
            self.scheduler._update_affected_wafers_in_chambers(wafer)

    def _pickup(self, robot: SchedulingRobot, task: TransportTask, wafer: SchedulingWafer):
        """执行抓取"""
        logging.info(
            f"1451 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})"
            f"(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"will move to {robot.robot_id} Slot 1")

        self._move_robot_to(robot, task.from_location)
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        logging.info(
            f"63 {task.from_location.replace('_', ' ', 1).replace('_', ' Slot ')} "
            f"INFO System Wafer(#{wafer.wafer_id})(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"moved to {robot.robot_id} Slot 1")

        # 释放源位置
        self._release_source(task.from_location, wafer.wafer_id)

    def _dropoff(self, robot: SchedulingRobot, task: TransportTask, wafer: SchedulingWafer):
        """执行放置"""
        #display_name = SystemConfig.CHAMBER_DISPLAY_NAMES.get(task.to_location.rstrip("_1"))
        logging.info(
            f"1451 {robot.robot_id} Slot 1 INFO System Wafer(#{wafer.wafer_id})(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"will move to {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')}")

        self._move_robot_to(robot, task.to_location)
        time.sleep(SystemConfig.PICK_PLACE_TIME)

        logging.info(
            f"63 {robot.robot_id} Slot 1 INFO System Wafer(#{wafer.wafer_id})(#{wafer.base.source_foup.value.replace('_', ' ')} {wafer.base.source_foup_slot.value}) "
            f"moved to {task.to_location.replace('_', ' ', 1).replace('_', ' Slot ')}")

    def _move_robot_to(self, robot: SchedulingRobot, location_id: str):
        """移动机械臂到指定位置（处理Z轴）"""
        target_z = LocationParser.get_z_level_for_location(location_id)

        if robot.current_z_level != target_z:
            robot.is_z_moving = True
            time.sleep(SystemConfig.Z_MOVE_TIME)
            robot.current_z_level = target_z
            robot.is_z_moving = False

    def _release_source(self, location_id: str, wafer_id: int):
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
        # 启动Chamber任务
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

        ll_base = ll.location_id.rsplit('_', 1)[0]
        begin_pump_id = '242' if ll_base=='LL_A' else '243'
        logging.info(f"{begin_pump_id} {ll_base} LL INFO LoadLock LoadLock A begin pumping...")

        def pump_complete():
            self.system.dry_pump_busy = 0
            self._set_ll_group_pumping(ll.location_id, False)

            end_pump_id = '246' if ll_base=='LL_A' else '247'
            logging.info(f"{end_pump_id} {ll_base} LL INFO LoadLock LoadLock A pumping end, spending time is {pump_duration}s")

            # 计算延迟 - 使用departure时间
            current_step = wafer.path_plan[wafer.current_step_index - 1]
            delay = time.time() - current_step.estimated_departure
            wait_time = max(0, -delay)

            self._log_deviation(wafer, ll.location_id, delay)

            def leave_ll():
                ll.busy = 0
                ll.current_wafer_id = None
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

        ll_base = ll.location_id.rsplit('_', 1)[0]
        begin_vent_id = '240' if ll_base=='LL_A' else '241'
        logging.info(f"{begin_vent_id} {ll_base} LL INFO LoadLock LoadLock B begin venting...")

        def vent_complete():
            self.system.dry_pump_busy = 0
            self._set_ll_group_pumping(ll.location_id, False)
            end_vent_id = '244' if ll_base=='LL_A' else '245'
            logging.info(f"{end_vent_id} {ll_base} LL INFO LoadLock LoadLock B venting end, spending time is {vent_duration}s")

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

    def _get_ll_group(self, ll_location_id: str) -> list:
        """获取LL所在的组（进片槽和出片槽为一组）"""
        base_module = LocationParser.get_base_module_id(ll_location_id)
        ll_group = []

        for ll_id, ll in self.system.lls.items():
            if LocationParser.get_base_module_id(ll_id) == base_module:
                ll_group.append(ll)

        return ll_group

    def _arrival_tbs(self, wafer: SchedulingWafer, tbs):
        """TBS到达"""

        logging.info(f"{LogIcon.TRANSPORT} Wafer {wafer.wafer_id} 到达 {tbs.location_id}")

        if tbs.has_cooling:
            # 需要冷却
            self._start_tbs_cooling(tbs, wafer)
        else:
            # 缓冲站：计算延迟并等待
            current_step = wafer.path_plan[wafer.current_step_index - 1]
            delay = time.time() - current_step.estimated_departure
            wait_time = max(0, -delay)

            self._log_deviation(wafer, tbs.location_id, delay)

            def leave_tbs():
                tbs.busy = 0
                tbs.booked_wafer_id = None
                tbs.current_wafer_id = None
                wafer.busy = 0
                self.scheduler.assign_next_task(wafer)

            if wait_time > 0:
                # 早到，需要等待
                threading.Timer(wait_time, leave_tbs).start()
            else:
                # 准时或晚到，直接离开
                leave_tbs()

    def _start_tbs_cooling(self, tbs, wafer: SchedulingWafer):
        """开始TBS冷却"""
        cooling_time = SystemConfig.COOLING_TIME_SECONDS

        logging.info(f"{LogIcon.PROCESS} 开始冷却: Wafer {wafer.wafer_id}: {tbs.location_id}，耗时{cooling_time}s")

        def cooling_thread():
            time.sleep(cooling_time)
            tbs.busy = 0
            tbs.booked_wafer_id = None
            tbs.current_wafer_id = None
            wafer.busy = 0

            logging.info(f"{LogIcon.PROCESS} Wafer {wafer.wafer_id} 在 {tbs.location_id} 冷却完成")
            self.scheduler.assign_next_task(wafer)

        threading.Thread(target=cooling_thread, daemon=True).start()

    def _log_deviation(self, wafer: SchedulingWafer, location_id: str, delay: float):
        """记录调度偏差"""
        if delay >= 0:
            logging.info(f"{LogIcon.PLAN} Wafer {wafer.wafer_id} 在 {location_id} 晚 {delay:.1f}s")
        else:
            logging.info(f"{LogIcon.WAIT} Wafer {wafer.wafer_id} 在 {location_id} 早 {-delay:.1f}s")