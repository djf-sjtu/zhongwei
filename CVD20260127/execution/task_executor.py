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
from domain import WaferService
from common import LogIcon


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
        logging.info(f"57 {chamber.location_id} Slot1 INFO System Launched {task.task_type} recipe")
        
        if task.task_type == 'wafer_process':
            logging.info(f"{LogIcon.PROCESS} 开始加工: Wafer {task.wafer_id}: "
                        f"在{chamber.location_id}加工，时长{task.duration:.1f}s")
        else:
            logging.info(f"{LogIcon.MACRO} 开始宏任务：{chamber.location_id} 开始{task.task_type}任务 "
                        f"(时长 {task.duration:.1f}s)")
            chamber.busy = 1  # 宏任务设置busy

        # 启动任务线程
        def task_thread():
            try:
                time.sleep(task.duration)
                logging.info(f"53 {chamber.location_id} Slot1 INFO System End of recipe")
                self._on_task_completed(chamber, task)
            except Exception as e:
                logging.error(f"Chamber任务执行错误: {e}")

        threading.Thread(target=task_thread, daemon=True).start()

    def _on_task_completed(self, chamber: SchedulingChamber, task: ChamberTask):
        """任务完成回调"""
        if task.task_type == 'wafer_process':
            with chamber._fault_lock:
                if chamber.is_faulted:
                    # chamber 故障中：冻结任务，保持 current_task 非空阻止新任务进入
                    chamber.frozen_task = task
                    logging.warning(f"❄️ Wafer {task.wafer_id} 任务冻结（chamber 故障中）: {chamber.location_id}")
                    return
            self._complete_wafer_task(chamber, task)
        elif task.task_type == 'macro':
            self._complete_macro_task(chamber, task)
            chamber.busy = 0

        # 清除当前任务
        chamber.current_task = None
        chamber.last_task_end_time = time.time()

        # 继续处理队列
        self.process_chamber_queue(chamber)

    def release_frozen_task(self, chamber: SchedulingChamber):
        """解冻任务，触发 wafer 继续流转（unfault 时调用）"""
        task = chamber.frozen_task
        if not task:
            return
        chamber.frozen_task = None
        logging.info(f"🔓 解冻 Wafer {task.wafer_id}，继续流转: {chamber.location_id}")
        self._complete_wafer_task(chamber, task)
        chamber.current_task = None
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
        return WaferService.calculate_film_consumption_from_recipe(
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
