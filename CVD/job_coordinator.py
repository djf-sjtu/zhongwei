import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# -*- coding: utf-8 -*-
# services/coordination/job_coordinator.py
"""
Job协调器
职责：Job队列管理、周期性检查协调、EFEM装载协调
"""
import time
import threading
import logging

from entities import SchedulingSystem, SchedulingWafer
from services.planning import PathPlanner
from services.scheduling import SchedulerCore, ResourceSelector
from execution import TransportExecutor, TaskExecutor, MacroManager
from config import SystemConfig, ConfigService
from common import LogIcon, FaultMetrics
from models import module_id_to_str


class JobCoordinator:
    """Job协调器 - 系统的最顶层编排"""

    def __init__(self):
        # 系统
        self.system = SchedulingSystem()
        self.metrics = FaultMetrics()

        # 服务层初始化（自下而上）
        self.resource_selector = ResourceSelector(self.system)
        self.scheduler_core = SchedulerCore(self.system, self.resource_selector)
        self.path_planner = PathPlanner(self.system)
        self.scheduler_core.metrics = self.metrics
        self.path_planner.metrics = self.metrics
        
        self.task_executor = TaskExecutor(self.system, self.scheduler_core)
        self.transport_executor = TransportExecutor(self.system, self.scheduler_core, self.path_planner)
        self.macro_manager = MacroManager(self.system, self.task_executor)

        # 依赖注入
        self.scheduler_core.transport_service = self.transport_executor
        self.scheduler_core.path_planner = self.path_planner
        self.transport_executor.task_executor = self.task_executor

        # 回调注入
        self.transport_executor.on_wafer_completed = self._on_wafer_completed
        self.transport_executor.on_wafer_loaded_callback = self._load_next_wafer

        # Job管理
        self.job_queue = []
        self.current_job = None
        self.current_job_wafer_index = 0

        # 运行状态
        self.running = False

        # 统计
        self.stats = {
            'total_cycles': 0,
            'successful_transports': 0,
            'failed_transports': 0,
            'jobs_completed': 0
        }

    # ================================================================
    # 系统控制
    # ================================================================

    def start_system(self):
        """启动系统"""
        if self.running:
            return

        self.running = True

        # 启动Job处理线程
        threading.Thread(
            target=self._job_processing_loop,
            name="JobProcessor",
            daemon=True
        ).start()

        # 启动周期检查线程
        threading.Thread(
            target=self._periodic_check_loop,
            name="PeriodicCheck",
            daemon=True
        ).start()

    def stop_system(self):
        """停止系统"""
        if not self.running:
            return

        logging.info("停止主调度系统...")
        self.running = False

    # ================================================================
    # Job处理循环
    # ================================================================

    def _job_processing_loop(self):
        """Job处理循环 - 顺序执行每个Job"""
        for job in self.job_queue:
            if not self.running:
                break

            logging.info(f"\n{LogIcon.SYSTEM} {'=' * 60}")
            logging.info(f"{LogIcon.SYSTEM} 开始处理 Job {job.process_job_id}")
            logging.info(f"{LogIcon.SYSTEM} {'=' * 60}")

            try:
                # 准备Job
                if not self._prepare_job(job):
                    logging.warning(f"Job {job.process_job_id} 准备失败，跳过")
                    self.stats['jobs_completed'] += 1
                    continue

                # 等待Job完成
                while self.current_job is not None and self.running:
                    time.sleep(2.0)

            except Exception as e:
                logging.error(f"Job {job.process_job_id} 处理错误: {e}")

        logging.info("\n🎉 所有Job处理完成")

    def _prepare_job(self, job) -> bool:
        """Job开始前的准备"""
        logging.info(f"=== 准备 Job {job.process_job_id} ===")

        # 1. 验证资源
        if not self.macro_manager.check_film_thickness_sufficient(job):
            logging.warning(f"Job {job.process_job_id} 膜厚不足")
            return False

        # 2. 安排机会清洗
        self._arrange_opportunistic_cleaning(job)

        # 3. 安排Prelot任务
        if job.sequence_for_job.pre_lot_recipe:
            self.macro_manager.arrange_prelot(job)

        # 4. 安排预热任务
        self._arrange_preheat(job)

        # 5. 初始化Job状态
        self.current_job = job
        self.current_job_wafer_index = 0
        self._load_next_wafer()

        # 对 MergedJob：强制从另一个子job加载，确保两个NUC同时填满
        from common import MergedJob
        if isinstance(job, MergedJob):
            # 检查第一次装载选了哪个job，从另一个job强制装载
            loaded_job1 = (job._job1_index > 0)
            if loaded_job1 and job._job2_index < len(job._job2.wafer_collection):
                wafer_base = job._job2.wafer_collection[job._job2_index]
                job.mark_wafer_loaded("job2")
                logging.info(f"{LogIcon.PLAN} 并行装载(初始): 强制从job2装载 Wafer {wafer_base.wafer_id}")
                self._start_wafer(wafer_base)
            elif not loaded_job1 and job._job1_index < len(job._job1.wafer_collection):
                wafer_base = job._job1.wafer_collection[job._job1_index]
                job.mark_wafer_loaded("job1")
                logging.info(f"{LogIcon.PLAN} 并行装载(初始): 强制从job1装载 Wafer {wafer_base.wafer_id}")
                self._start_wafer(wafer_base)

        logging.info(f"Job {job.process_job_id} 准备完成")
        return True

    def _arrange_opportunistic_cleaning(self, job):
        """安排机会清洗"""
        unused_chambers = self.macro_manager.get_unused_chambers(job)

        # 按工艺类型分组
        chambers_by_type = {}
        for chamber in unused_chambers:
            ptype = ConfigService.get_process_type(module_id_to_str(chamber.base.module_id))
            if ptype not in chambers_by_type:
                chambers_by_type[ptype] = []
            chambers_by_type[ptype].append(chamber)

        # 每种工艺保留膜厚最低的，清洗其他的
        for ptype, chambers in chambers_by_type.items():
            chambers.sort(key=lambda c: c.base.film_thickness_counter)
            to_clean = chambers[1:]  # 排除第一个

            for chamber in to_clean:
                if chamber.base.film_thickness_counter > 75:
                    self.macro_manager.arrange_cleaning(chamber)
                    logging.info(f"{LogIcon.MACRO} 机会清洗: {chamber.location_id}")

    def _arrange_preheat(self, job):
        """安排预热任务"""
        chambers = self.macro_manager.get_target_chambers(job)
        for chamber in chambers:
            if chamber.needs_immediate_preheat():
                task = self.macro_manager.create_preheat_task(chamber)
                if task:
                    self.task_executor.push_macro_task(chamber, task)
                    logging.info(f"{LogIcon.MACRO} 预热任务: {chamber.location_id}")

    def _cleanup_job(self, job):
        """Job完成后的清理"""
        logging.info(f"=== 清理 Job {job.process_job_id} ===")

        if job.sequence_for_job.post_lot_recipe:
            chambers = self.macro_manager.get_chambers_from_recipe(
                job.sequence_for_job.post_lot_recipe
            )
            for chamber in chambers:
                task = self.macro_manager.create_postlot_task(
                    job.sequence_for_job.post_lot_recipe,
                    chamber
                )
                self.task_executor.push_macro_task(chamber, task)

    def _on_wafer_completed(self, wafer: SchedulingWafer):
        """Wafer完成回调"""
        from common import MergedJob

        self.metrics.on_wafer_completed(wafer.wafer_id)

        if not self.current_job:
            return

        job = self.current_job

        if isinstance(job, MergedJob):
            # 并行任务：分别检查两个子job
            self._check_subjob(job._job1, "SubJob1")
            self._check_subjob(job._job2, "SubJob2")

            # 检查整个MergedJob是否完成
            if self._is_job_completed(job):
                logging.info(f"\n{LogIcon.COMPLETE} MergedJob {job.process_job_id} 全部完成")
                self.current_job = None
                self.stats['jobs_completed'] += 1
        else:
            # 普通任务
            if self._is_job_completed(job):
                self._cleanup_job(job)
                self.stats['jobs_completed'] += 1
                logging.info(f"{LogIcon.COMPLETE} Job {job.process_job_id} 完成")
                self.current_job = None

    def _check_subjob(self, subjob, subjob_name: str):
        """检查并清理子job"""
        if not hasattr(subjob, '_cleaned') and self._is_job_completed(subjob):
            self._cleanup_job(subjob)
            subjob._cleaned = True
            logging.info(f"{LogIcon.COMPLETE} {subjob_name} ({subjob.process_job_id}) 完成")

    def _is_job_completed(self, job) -> bool:
        """检查Job是否完成"""
        for wafer_base in job.wafer_collection:
            wafer = self.system.wafers.get(wafer_base.wafer_id)
            if wafer and not wafer.is_sequence_completed():
                return False
        return True

    # ================================================================
    # 周期性检查
    # ================================================================

    def _periodic_check_loop(self):
        """周期性检查循环"""
        check_interval = SystemConfig.SCHEDULING_CONFIG.get('periodic_check_interval', 1.0)

        while self.running:
            time.sleep(check_interval)
            self.stats['total_cycles'] += 1
            self.scheduler_core.check_waiting_wafers()

    # ================================================================
    # EFEM装载
    # ================================================================

    def _load_next_wafer(self):
        """装载下一片wafer"""
        if not self.current_job:
            return

        job = self.current_job
        from common import MergedJob

        if isinstance(job, MergedJob):
            # 并行任务：动态选择
            best_wafer, best_job_name = self._select_wafer_for_merged_job(job)
            if not best_wafer:
                return
            job.mark_wafer_loaded(best_job_name)
            logging.info(f"{LogIcon.PLAN} 并行装载: 选择{best_job_name}的Wafer {best_wafer.wafer_id}")
        else:
            # 普通Job：顺序装载
            if self.current_job_wafer_index >= len(job.wafer_collection):
                return
            best_wafer = job.wafer_collection[self.current_job_wafer_index]
            self.current_job_wafer_index += 1

        self._start_wafer(best_wafer)

    def _start_wafer(self, wafer_base):
        """将wafer加入系统、规划路径并定时加入等待队列"""
        wafer = self.system.add_wafer(wafer_base)

        if not self.path_planner.plan_wafer_route_from_foup(wafer):
            logging.error(f"{LogIcon.ERROR} Wafer {wafer.wafer_id} 路径规划失败")
            return

        delay = max(wafer.scheduled_leave_time - time.time(), 0)

        def start_loading():
            self.system.waiting_wafers.append(wafer)

        if delay > 0:
            threading.Timer(delay, start_loading).start()
        else:
            start_loading()

    def _select_wafer_for_merged_job(self, job):
        """为并行job选择下一片wafer"""
        if not job.has_next_wafer():
            return None, None

        candidates = job.get_next_wafer_candidates()

        if len(candidates) == 1:
            return candidates[0]

        # 选择leave_time最早的
        best_wafer = None
        best_job_name = None
        min_leave_time = float('inf')

        for wafer_base, job_name in candidates:
            # 只计算时间，不预订资源
            leave_time = self.path_planner.calculate_leave_time_without_reservation(wafer_base)

            if leave_time < min_leave_time:
                min_leave_time = leave_time
                best_wafer = wafer_base
                best_job_name = job_name

        return best_wafer, best_job_name
    # ================================================================
    # 状态查询
    # ================================================================

    def get_system_status(self) -> dict:
        """获取系统状态"""
        completed_count = sum(1 for w in self.system.wafers.values()
                            if w.is_sequence_completed())

        return {
            'system_running': self.running,
            'stats': self.stats.copy(),
            'job_status': {
                'total_jobs': len(self.job_queue),
                'completed_jobs': self.stats['jobs_completed'],
                'current_job': self.current_job.process_job_id if self.current_job else None,
            },
            'wafer_counts': {
                'total': len(self.system.wafers),
                'completed': completed_count,
                'waiting': len(self.system.waiting_wafers)
            },
            'robot_status': {
                rid: {
                    'available': r.is_robot_available(),
                    'z_level': r.current_z_level,
                    'queue_length': len(r.transport_queue)
                }
                for rid, r in self.system.robots.items()
            }
        }

    # ================================================================
    # 故障管理
    # ================================================================

    def fault_chamber(self, chamber_id: str) -> bool:
        """将 chamber 标记为故障，并为所有受影响 wafer 重规划"""
        chamber = self.system.chambers.get(chamber_id)
        if not chamber:
            logging.warning(f"fault_chamber: 找不到 chamber {chamber_id}")
            return False

        with chamber._fault_lock:
            chamber.is_faulted = True

        logging.warning(f"⚠️ Chamber 故障: {chamber_id}")
        for wafer in self.system.wafers.values():
            if wafer.current_location_id == chamber_id:
                self.metrics.ignore_wafer(wafer.wafer_id)
        self.metrics.snapshot('faulted', chamber_id)

        process_type = ConfigService.get_process_type(chamber_id)
        strategy = SystemConfig.SCHEDULING_CONFIG.get('fault_replan_strategy', 'continuity_pool')
        if strategy == 'legacy_direct':
            replanned = self._legacy_replan_faulted_chamber(chamber_id)
        else:
            replanned = self.path_planner.replan_process_type_for_fault(process_type, chamber_id)
        if replanned:
            logging.info(f"{LogIcon.PLAN} 故障工艺池重规划完成：{replanned} 片 wafer 重排")
        return True

    def _legacy_replan_faulted_chamber(self, chamber_id: str) -> int:
        """Original behavior: only reroute wafers reserved on the failed chamber."""
        chamber = self.system.chambers.get(chamber_id)
        if not chamber:
            return 0
        affected = [
            self.system.wafers.get(t.wafer_id)
            for t in chamber.task_queue
            if t.task_type == 'wafer_process' and t.wafer_id is not None
        ]
        considered = [wafer.wafer_id for wafer in affected if wafer]
        self.metrics.on_fault_candidates(chamber_id, considered)
        replanned = 0
        for wafer in affected:
            if wafer and self.path_planner.replan_for_faulted_chamber(wafer, chamber_id):
                replanned += 1
        return replanned

    def unfault_chamber(self, chamber_id: str) -> bool:
        """恢复 chamber，重新接受 wafer"""
        chamber = self.system.chambers.get(chamber_id)
        if not chamber:
            logging.warning(f"unfault_chamber: 找不到 chamber {chamber_id}")
            return False

        with chamber._fault_lock:
            chamber.is_faulted = False

        logging.info(f"✅ Chamber 故障修复: {chamber_id}")

        # 处理冻结任务：故障期间完成加工但被冻结的 wafer 现在继续流转
        if chamber.frozen_task:
            self.task_executor.release_frozen_task(chamber)

        self.metrics.snapshot('before_unfault_rebalance', chamber_id)

        # 重平衡：恢复同工艺产能后，按连续性目标重新规划剩余路径
        process_type = ConfigService.get_process_type(chamber_id)
        strategy = SystemConfig.SCHEDULING_CONFIG.get('fault_replan_strategy', 'continuity_pool')
        if strategy != 'legacy_direct':
            self.path_planner.rebalance_process_type_after_unfault(process_type)
        self.metrics.snapshot('after_unfault_rebalance', chamber_id)

        return True
