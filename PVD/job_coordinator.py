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
from common import LogIcon, trace_writer
from models import module_id_to_str


class JobCoordinator:
    """Job协调器 - 系统的最顶层编排"""

    def __init__(self):
        # 系统
        self.system = SchedulingSystem()

        # 服务层初始化（自下而上）
        self.resource_selector = ResourceSelector(self.system)
        self.scheduler_core = SchedulerCore(self.system, self.resource_selector)
        self.path_planner = PathPlanner(self.system)
        
        self.task_executor = TaskExecutor(self.system, self.scheduler_core)
        self.transport_executor = TransportExecutor(self.system, self.scheduler_core)
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

        # 等价性 trace：wafer 走完整个 sequence 回到 FOUP 的终点事件
        trace_writer.emit('wafer_completed', wafer_id=wafer.wafer_id)

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

        # 添加wafer到系统并规划路径
        wafer = self.system.add_wafer(best_wafer)

        if not self.path_planner.plan_wafer_route_from_foup(wafer):
            logging.error(f"{LogIcon.ERROR} Wafer {wafer.wafer_id} 路径规划失败")
            return

        # 定时启动装载
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
    # Chamber 故障外部信号（v1）
    # ================================================================

    def fault_chamber(self, chamber_id: str) -> bool:
        """把 chamber 标记为故障：不再接受新 wafer。已在加工中的 wafer 任务被
        冻结（thread 醒来后看到 is_faulted 会把 task 转到 frozen_task），等
        unfault_chamber 触发完成。

        v1 不重规划已规划但未走到 chamber 的 wafer（由 #02 issue 处理）。

        Args:
            chamber_id: SchedulingChamber.location_id（如 "PVD1_A_1"）
        Returns:
            True 表示状态切换成功；False 表示 chamber_id 不存在
        """
        chamber = self.system.chambers.get(chamber_id)
        if chamber is None:
            logging.warning(f"{LogIcon.WARNING} fault_chamber: 未找到 chamber {chamber_id}")
            return False

        with chamber._fault_lock:
            if chamber.is_faulted:
                return True  # 幂等
            chamber.is_faulted = True

        logging.info(f"{LogIcon.WARNING} Chamber 故障: {chamber_id}")
        trace_writer.emit('chamber_faulted', chamber_id=chamber_id)

        # 扫所有 in-flight wafer，对下游含故障 chamber 的（且自己不卡在里面的）触发重规划。
        # 卡在故障 chamber 里的 wafer 不参与（它的出片时间由 unfault 决定）。
        affected = 0
        for wafer in list(self.system.wafers.values()):
            if wafer.current_location_id == chamber_id:
                continue
            if not wafer.path_plan:
                continue
            has_downstream_fault = any(
                step.location_id == chamber_id
                for step in wafer.path_plan[wafer.current_step_index:]
            )
            if not has_downstream_fault:
                continue
            if self.path_planner.replan_for_faulted_chamber(wafer, chamber_id):
                affected += 1

        if affected > 0:
            logging.info(f"{LogIcon.PLAN} 故障重规划完成：{affected} 片 wafer 改道")
        return True

    def unfault_chamber(self, chamber_id: str) -> bool:
        """把 chamber 标记为故障已修复：如果里面卡着 wafer 任务，立即视为完成，
        wafer 按正常流程出片并推进 sequence。如果 chamber 是空闲故障，仅恢复 online。

        Args:
            chamber_id: SchedulingChamber.location_id
        Returns:
            True 表示状态切换成功；False 表示 chamber_id 不存在
        """
        chamber = self.system.chambers.get(chamber_id)
        if chamber is None:
            logging.warning(f"{LogIcon.WARNING} unfault_chamber: 未找到 chamber {chamber_id}")
            return False

        task_to_complete = None
        with chamber._fault_lock:
            if not chamber.is_faulted:
                return True  # 幂等
            chamber.is_faulted = False
            # 优先 frozen_task（task_thread 已经在 fault 期间醒来过、被冻结住）
            if chamber.frozen_task is not None:
                task_to_complete = chamber.frozen_task
                chamber.frozen_task = None
            # 否则 current_task（task_thread 还在 sleep，preempt 它：清空 current_task，
            # thread 醒来后 _on_task_completed 入口的 `chamber.current_task is not task`
            # 守卫会让它 noop）
            elif chamber.current_task is not None:
                task_to_complete = chamber.current_task
                chamber.current_task = None

        # v1：只处理 wafer 任务的"视为完成"。macro 任务在 fault 期间继续完成（_on_task_completed
        # 不会冻结 macro），所以这里看到的 task_to_complete 若是 macro，说明 fault 期间 macro 还没跑完，
        # 当前简化处理 = 丢弃 task（不触发 macro 完成回调）。极端 corner case，v1 不展开。
        if task_to_complete is not None and task_to_complete.task_type == 'wafer_process':
            self.task_executor.force_complete_wafer_task(chamber, task_to_complete)

        logging.info(f"{LogIcon.COMPLETE} Chamber 故障修复: {chamber_id}")
        trace_writer.emit('chamber_unfaulted', chamber_id=chamber_id)

        # 修复后重新评估：将已被改道到兄弟 chamber 的 wafer 重新择优分流
        self._rebalance_after_unfault(chamber_id)
        return True

    def _rebalance_after_unfault(self, chamber_id: str):
        """chamber 修复后，把已改道到同类兄弟 chamber 的 wafer 重新择优。
        复用 replan_for_faulted_chamber 的最优选择逻辑：每次调用都读实时 task_queue，
        自然实现逐步负载均衡（修复 chamber 从空队列开始，逐渐接收 wafer，
        直到与兄弟 chamber 的队列深度相当）。"""
        module_str = chamber_id.rsplit('_', 1)[0]   # "CVD2_A_1" → "CVD2_A"
        process_type = ConfigService.get_process_type(module_str)
        sibling_module_ids = ConfigService.get_modules_by_process_type(process_type)
        # 兄弟 chamber 的 location_id（排除刚修复的自身）
        sibling_location_ids = {
            f"{m}_1" for m in sibling_module_ids
            if f"{m}_1" != chamber_id
        }
        if not sibling_location_ids:
            return

        # 收集指向兄弟 chamber 的 wafer，按实时 ETA 升序排列
        # 用实时位置推算（而非旧 estimated_arrival 时间戳）确保排序准确：
        # 仍在加工的 wafer ETA 较晚，等待中的 wafer ETA 较早，贪心分配自然均衡负载
        candidates = []
        for wafer in list(self.system.wafers.values()):
            if not wafer.path_plan:
                continue
            for step in wafer.path_plan[wafer.current_step_index:]:
                if step.location_id in sibling_location_ids:
                    eta = self.path_planner._estimate_current_arrival(wafer, step.location_id)
                    candidates.append((eta, wafer, step.location_id))
                    break
        candidates.sort(key=lambda x: x[0])

        redirected = 0
        for _, wafer, target_sibling in candidates:
            if self.path_planner.replan_for_faulted_chamber(wafer, target_sibling):
                redirected += 1

        if redirected > 0:
            logging.info(
                f"{LogIcon.PLAN} unfault 重调度: {redirected} 片 wafer 重新择优 chamber"
            )

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
