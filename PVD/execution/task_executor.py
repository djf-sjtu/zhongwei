# -*- coding: utf-8 -*-
# services/execution/task_executor.py
"""
任务执行器
职责：Chamber和Aligner任务的管理与执行
"""
import time
import threading
import logging

from entities import SchedulingChamber, SchedulingAligner, SchedulingWafer, ChamberTask
from config import ConfigService
from utils import LocationParser
from common import LogIcon, trace_writer


class TaskExecutor:
    """任务执行器 - 负责Chamber和Aligner任务"""

    def __init__(self, system, scheduler_core):
        self.system = system
        self.scheduler = scheduler_core

    # ================================================================
    # Chamber任务管理
    # ================================================================

    def start_chamber_task(self, chamber: SchedulingChamber, wafer: SchedulingWafer):
        """启动Chamber任务"""
        duration = self._get_duration(wafer, chamber)

        # 移除预订任务（如果存在）
        if chamber.task_queue and chamber.task_queue[0].wafer_id == wafer.wafer_id:
            chamber.task_queue.pop(0)

        # 创建wafer任务并插入队首
        task = ChamberTask(
            task_id=f"wafer_{wafer.wafer_id}_{int(time.time() * 1000)}",
            task_type='wafer_process',
            wafer_id=wafer.wafer_id,
            duration=duration
        )

        chamber.task_queue.insert(0, task)
        self.process_chamber_queue(chamber)

    def _get_duration(self, wafer: SchedulingWafer, chamber: SchedulingChamber) -> float:
        """获取任务时长"""
        current_step = wafer.get_current_step()
        if not current_step:
            return 60.0

        # 优先从recipe获取
        recipe = current_step.process_recipe
        if recipe and recipe.recipe_views:
            return recipe.recipe_views[0].recipe_max_time_seconds

        # 从配置获取
        process_type = ConfigService.get_process_type(chamber.location_id)
        return ConfigService.get_process_time(process_type, 120.0)

    def process_chamber_queue(self, chamber: SchedulingChamber):
        """处理Chamber任务队列"""
        # 有任务正在执行
        if chamber.current_task:
            return

        # 队列为空
        if not chamber.task_queue:
            return

        task = chamber.task_queue[0]

        # 宏任务 - 直接执行
        if task.task_type == 'macro':
            chamber.task_queue.pop(0)
            self._execute_task(chamber, task)

        # Wafer任务 - 检查wafer是否到达
        elif task.task_type == 'wafer_process':
            if task.wafer_id == chamber.current_wafer_id:
                chamber.task_queue.pop(0)
                self._execute_task(chamber, task)

    def _execute_task(self, chamber: SchedulingChamber, task: ChamberTask):
        """执行Chamber任务"""
        chamber.current_task = task
        chamber.task_start_time = time.time()

        # 日志

        if task.task_type == 'wafer_process':
            logging.info(f"{LogIcon.PROCESS} 开始加工: Wafer {task.wafer_id}: "
                        f"在{chamber.location_id}加工，时长{task.duration:.1f}s")
            logging.info(f"57 {chamber.location_id} Slot1 INFO System Launched {task.task_type} recipe: wafer_process.rcp")

            # 微小偏移确保并发 chamber_task_started trace 事件按 wafer_id 升序排列
            time.sleep(task.wafer_id % 1000 * 0.01)
            # 等价性 trace：chamber 任务启动（确认 wafer 真正进入加工阶段）
            trace_writer.emit(
                'chamber_task_started',
                wafer_id=task.wafer_id,
                chamber_id=chamber.location_id,
            )
        else:
            logging.info(f"{LogIcon.MACRO} 开始宏任务：{chamber.location_id} 开始{task.task_type}任务 "
                        f"(时长 {task.duration:.1f}s)")
            logging.info(f"57 {chamber.location_id} Slot1 INFO System Launched Idle Clean recipe: PMG_Idle Purge_V3_071825.rcp")
            chamber.busy = 1  # 宏任务设置busy

        # 启动任务线程
        def task_thread():
            try:
                time.sleep(task.duration)
                if task.task_type == 'wafer_process':
                    logging.info(f"53 {chamber.location_id} Slot1 INFO System End of recipe: wafer_process.rcp")
                else:
                    logging.info(f"53 {chamber.location_id} Slot1 INFO System End of recipe: Pretreat_burnin_X200_052725.rcp")
                self._on_task_completed(chamber, task)
            except Exception as e:
                logging.error(f"Chamber任务执行错误: {e}")

        threading.Thread(target=task_thread, daemon=True).start()

    def _on_task_completed(self, chamber: SchedulingChamber, task: ChamberTask):
        """任务完成回调

        故障穿插语义（v1）：
        - 如果 chamber 在 fault 状态、且 task 是 wafer_process，把 task 冻结到
          chamber.frozen_task，不走完成流程；等 unfault_chamber 触发完成。
        - 如果 chamber.current_task 已不再是当前 task（说明 unfault_chamber 已经
          抢先调 _complete_wafer_task），本回调 noop。
        - macro 任务不参与 fault 暂存，照常完成（v1 限制：不处理 fault 时正在跑
          macro 的情况，行为按"macro 自行跑完"）。
        """
        with chamber._fault_lock:
            # unfault preempt 已经处理过这个 task
            if chamber.current_task is not task:
                return
            # wafer 任务遇上 fault：冻结，等 unfault
            if chamber.is_faulted and task.task_type == 'wafer_process':
                chamber.frozen_task = task
                chamber.current_task = None
                return

        if task.task_type == 'wafer_process':
            self._complete_wafer_task(chamber, task)
        elif task.task_type == 'macro':
            self._complete_macro_task(chamber, task)
            chamber.busy = 0

        # 清除当前任务
        chamber.current_task = None
        chamber.last_task_end_time = time.time()

        # 继续处理队列
        self.process_chamber_queue(chamber)

        # 若 chamber 此时仍完全空闲，尝试从兄弟 chamber 抢活以避免闲置
        if (task.task_type == 'wafer_process'
                and not chamber.current_task
                and not chamber.task_queue):
            self.scheduler._steal_work_for_idle_chamber(chamber)

    def force_complete_wafer_task(self, chamber: SchedulingChamber, task: ChamberTask):
        """由外部（如 unfault_chamber）触发的 wafer 任务"立即视为完成"。

        语义等同于 _on_task_completed 内的 wafer_process 分支，但跳过 fault
        守卫（调用方必须自己保证 chamber 处于可以完成的状态，且 task 已经从
        chamber 的 current_task / frozen_task 中摘除）。
        """
        self._complete_wafer_task(chamber, task)
        chamber.last_task_end_time = time.time()
        self.process_chamber_queue(chamber)

    def _complete_wafer_task(self, chamber: SchedulingChamber, task: ChamberTask):
        """Wafer任务完成"""
        wafer = self.system.wafers.get(task.wafer_id)
        if not wafer:
            return

        # 更新wafer状态
        wafer.busy = 0
        wafer.storage_start_time = time.time()
        if self.scheduler.metrics:
            self.scheduler.metrics.on_process_completed(wafer.wafer_id)

        # 消耗膜厚
        film_consumption = self._calculate_film_consumption(wafer, chamber)
        chamber.base.film_thickness_counter += film_consumption

        # 记录历史
        if chamber.task_start_time:
            chamber.processing_history.append((chamber.task_start_time, time.time()))

        logging.info(f"{LogIcon.PROCESS}{LogIcon.COMPLETE} 完成加工: Wafer {task.wafer_id}: "
                    f"在{chamber.location_id}加工完成")

        # 分配下一任务
        self.scheduler.assign_next_task(wafer)

    def _calculate_film_consumption(self, wafer: SchedulingWafer, chamber: SchedulingChamber) -> int:
        """计算膜厚消耗"""
        current_step = wafer.get_current_step()
        if not current_step:
            return 1

        base_module = LocationParser.get_base_module_id(chamber.location_id)
        return ConfigService.calculate_film_consumption_from_recipe(
            current_step.process_recipe, base_module
        )

    def _complete_macro_task(self, chamber: SchedulingChamber, task: ChamberTask):
        """宏任务完成"""
        logging.info(f"{LogIcon.PROCESS}{LogIcon.COMPLETE} 完成宏任务: "
                    f"{chamber.location_id}宏任务完成")
        
        # 清洗任务 - 重置膜厚
        if hasattr(task, 'macro_task') and task.macro_task.task_type == 'cleaning':
            chamber.base.film_thickness_counter = 0
            chamber.last_cleaning_time = time.time()
            logging.info(f"{LogIcon.MACRO}{LogIcon.COMPLETE} 清洗完成: "
                        f"{chamber.location_id}膜厚已重置")

    # ================================================================
    # Aligner任务管理
    # ================================================================

    def start_aligner_task(self, aligner: SchedulingAligner, wafer: SchedulingWafer):
        """启动Aligner任务"""
        duration = ConfigService.get_aligner_process_time()
        aligner.current_wafer_start_time = time.time()

        logging.info(f"{LogIcon.PROCESS} 开始对准: Wafer {wafer.wafer_id}: "
                    f"在{aligner.location_id}开始对准")

        def aligner_thread():
            try:
                time.sleep(duration)
                self._on_aligner_completed(aligner, wafer.wafer_id)
            except Exception as e:
                logging.error(f"Aligner任务执行错误: {e}")

        threading.Thread(target=aligner_thread, daemon=True).start()

    def _on_aligner_completed(self, aligner: SchedulingAligner, wafer_id: int):
        """Aligner任务完成"""
        wafer = self.system.wafers.get(wafer_id)
        if wafer:
            wafer.busy = 0
            logging.info(f"{LogIcon.PROCESS}{LogIcon.COMPLETE}完成对准: Wafer {wafer_id}: "
                        f"在{aligner.location_id}完成对准")
            
            # 分配下一任务
            self.scheduler.assign_next_task(wafer)

    # ================================================================
    # 宏任务推送
    # ================================================================

    def push_macro_task(self, chamber: SchedulingChamber, macro_task):
        """推送宏任务到Chamber"""
        task = ChamberTask(
            task_id=macro_task.task_id,
            task_type='macro',
            macro_task=macro_task,
            duration=macro_task.duration
        )

        chamber.task_queue.append(task)
        self.process_chamber_queue(chamber)
