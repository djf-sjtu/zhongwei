import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# -*- coding: utf-8 -*-
# main_scheduler.py - 主调度系统入口 (CVD平台版本)
"""
半导体制造调度系统 - 主入口
使用重构后的模块化架构
CVD平台: 支持批处理wafer
"""
import time
import logging
import random
import threading
from models import (
    Wafer, ProcessJob,
    WaferAngle, SlotID, Sequence, SequenceStep, Recipe, RecipeView,
    ModuleID, module_id_to_str
)
from entities.batch_wafer import create_batches_from_wafers  # ★ CVD: 批处理支持
from job_coordinator import JobCoordinator
from config import SystemConfig, ConfigService
from common import setup_logger, LogIcon, merge_jobs

setup_logger()


class SchedulerSimulation:
    """调度系统仿真器"""

    def __init__(self):
        self.coordinator = JobCoordinator()

    # ================================================================
    # Job创建
    # ================================================================

    def create_job(self, job_id: int, sequence_type: str, wafer_count: int = 10) -> ProcessJob:
        """创建测试Job (CVD平台: 使用批处理)"""
        sequence = self._create_sequence(f"test_sequence_{job_id}", sequence_type)
        job = ProcessJob(
            process_job_id=job_id,
            sequence_for_job=sequence,
            wafer_collection=[]
        )

        # ★ CVD: 先创建所有单片wafer
        wafers = []
        foup_ids = list(SystemConfig.FOUP_MODULES.keys())
        job_angle = WaferAngle.ANGLE_1

        current_foup_index = (job_id - 1) % len(foup_ids)
        current_slot = 1

        for i in range(wafer_count):
            # 每个FOUP最多25个槽位
            if current_slot > 25:
                current_foup_index = (current_foup_index + 1) % len(foup_ids)
                current_slot = 1

            wafer = Wafer(
                wafer_id=job_id * 1000 + i,
                source_foup=ModuleID(foup_ids[current_foup_index]),
                source_foup_slot=SlotID(current_slot),
                associated_sequence=sequence,
                angle=job_angle
            )
            wafers.append(wafer)
            current_slot += 1

        # ★ CVD: 转换为批处理
        batches = create_batches_from_wafers(wafers)
        job.wafer_collection = batches

        batch_count = sum(1 for b in batches if hasattr(b, 'batch_id'))
        single_count = len(batches) - batch_count

        logging.info(f"创建 Job {job_id} ({sequence_type}): {wafer_count} wafers → "
                     f"{batch_count} batches" +
                     (f" + {single_count} single" if single_count > 0 else ""))
        return job

    def _create_sequence(self, name: str, sequence_type: str) -> Sequence:
        """创建工艺序列"""
        process_flows = SystemConfig.COMMON_PROCESS_SEQUENCES

        if sequence_type == 'complex':
            flow = process_flows['complex_flow']
        elif sequence_type == 'simple':
            flow = process_flows['simple_flow']
        else:
            flow = process_flows['basic_flow']

        sequence = Sequence(sequence_name=name, sequence_steps=[])

        for step_id, process_type in enumerate(flow):
            target_modules = ConfigService.get_modules_by_process_type(process_type)

            # 转换为ModuleID枚举
            target_module_enums = []
            for module_str in target_modules:
                try:
                    target_module_enums.append(ModuleID(module_str))
                except KeyError:
                    logging.warning(f"模块 {module_str} 不存在，跳过")

            if not target_module_enums:
                continue

            # 创建recipe
            recipe = Recipe(recipe_name=f"recipe_{process_type}", recipe_views=[])

            for module_enum in target_module_enums:
                process_time = ConfigService.get_process_time(process_type, 60.0)
                film_consumption = SystemConfig.PROCESS_FILM_CONSUMPTION.get(process_type, 1)

                recipe_view = RecipeView(
                    module_id=module_enum,
                    recipe_max_time_seconds=process_time,
                    recipe_step_count=1,
                    film_thickness_on_station=[film_consumption, 0]  # ← 添加这个参数
                )
                recipe.recipe_views.append(recipe_view)

            # 创建步骤
            step = SequenceStep(
                step_id=step_id,
                target_modules=target_module_enums,
                process_recipe=recipe
            )
            sequence.sequence_steps.append(step)

        # ✅ 添加prelot和postlot
        sequence.pre_lot_recipe = self._create_lot_recipe(sequence, "prelot")
        sequence.pre_lot_recipe_condition_chamber_idle_time = random.uniform(300, 600)

        sequence.post_lot_recipe = self._create_lot_recipe(sequence, "postlot")

        return sequence

    def _create_lot_recipe(self, sequence: Sequence, recipe_type: str) -> Recipe:
        """创建prelot/postlot recipe"""
        # 收集sequence中所有的chamber模块
        job_chambers = []
        for step in sequence.sequence_steps:
            if step.target_modules:
                for module_id in step.target_modules:
                    module_str = module_id_to_str(module_id)
                    if ConfigService.is_chamber_module(module_str):
                        job_chambers.append(module_str)

        recipe = Recipe(recipe_name=f"{recipe_type}_recipe", recipe_views=[])

        if job_chambers:
            # prelot只在NUC腔运行（BULK prelot时长=BULK工艺时间，会严重延迟路径规划）
            nuc_chambers = [ch for ch in job_chambers
                            if ConfigService.get_process_type(ch) == 'NUC']
            pool = nuc_chambers if nuc_chambers else job_chambers
            selected_chambers = random.sample(pool, min(2, len(pool)))
            for selected_chamber in selected_chambers:
                # 获取工艺时间
                process_type = ConfigService.get_process_type(selected_chamber)
                process_time = ConfigService.get_process_time(process_type, 120.0)

                recipe_view = RecipeView(
                    module_id=ModuleID(selected_chamber),
                    recipe_max_time_seconds=process_time,
                    recipe_step_count=1,
                    film_thickness_on_station=[0, 0]  # lot recipe不消耗膜厚
                )
                recipe.recipe_views.append(recipe_view)

        return recipe

    # ================================================================
    # 仿真运行
    # ================================================================

    def run(self, jobs: list):
        """运行仿真"""
        logging.info(f"\n{'=' * 80}")
        logging.info(f"{LogIcon.SYSTEM} 开始调度仿真 - {len(jobs)} 个Job")
        logging.info(f"{'=' * 80}\n")

        start_time = time.time()
        self.coordinator.metrics.start()

        # 添加Job到队列
        self.coordinator.job_queue = jobs

        # 启动系统
        self.coordinator.start_system()

        # 等待完成
        while self.coordinator.running:
            time.sleep(5.0)

            status = self.coordinator.get_system_status()

            if status['job_status']['completed_jobs'] >= len(jobs):
                break

        # 停止系统
        self.coordinator.stop_system()
        self.coordinator.metrics.refresh_open_storage(self.coordinator.system)
        self.coordinator.metrics.finish()

        # 输出统计
        end_time = time.time()
        duration = end_time - start_time

        logging.info(f"\n{'=' * 80}")
        logging.info(f"{LogIcon.COMPLETE} 仿真完成")
        logging.info(f"{'=' * 80}")
        logging.info(f"总耗时: {duration:.1f}秒")
        logging.info(f"Job数量: {len(jobs)}")
        logging.info(f"Wafer总数: {sum(len(j.wafer_collection) for j in jobs)}")
        logging.info(f"调度周期数: {self.coordinator.stats['total_cycles']}")
        logging.info(f"{'=' * 80}\n")
        self.coordinator.metrics.log_summary()


# ================================================================
# 测试场景
# ================================================================

def scenario_1_single_job():
    """场景1: 单个Job"""
    logging.info("\n=== 场景1: 单个Job（简单工艺） ===")

    sim = SchedulerSimulation()
    job = sim.create_job(job_id=1, sequence_type='basic', wafer_count=5)
    sim.run([job])


def scenario_2_fault_chamber_basic():
    """场景2: 单个Job + chamber 故障/恢复（issue #01 验收）"""
    logging.info("\n=== 场景2: 单个Job + chamber 故障（T=60s）===")

    sim = SchedulerSimulation()
    job = sim.create_job(job_id=1, sequence_type='basic', wafer_count=5)

    timers = [
        threading.Timer(60.0,  sim.coordinator.fault_chamber,   args=('BULK_A',)),
        threading.Timer(180.0, sim.coordinator.unfault_chamber, args=('BULK_A',)),
    ]
    for t in timers:
        t.daemon = True
        t.start()

    try:
        sim.run([job])
    finally:
        for t in timers:
            t.cancel()


def scenario_3_fault_combined():
    """场景3: 联合验收 issue #02 重规划 + #03 冻结 + #04 TEMP_PARK
    两个Job合并(MergedJob)，各6片wafer，t=200s注入 BULK_A 故障，t=600s恢复。
    - 两个NUC同时填满 → 两个batch同时完成NUC → race → 触发 TEMP_PARK（#04）
    - t=200s 时 batch1-job1 正在 BULK_A 加工，unfault=600s > 加工完成时间 → 触发冻结（#03）
    - t=200s 时 batch2 已预订 BULK_A 但还在路上 → 触发重规划（#02）
    """
    logging.info("\n=== 场景3: 联合故障验收（#02+#03+#04）===")

    sim = SchedulerSimulation()
    job1 = sim.create_job(job_id=1, sequence_type='basic', wafer_count=6)
    job2 = sim.create_job(job_id=2, sequence_type='basic', wafer_count=6)
    merged = merge_jobs(job1, job2)

    timers = [
        threading.Timer(200.0, sim.coordinator.fault_chamber,   args=('BULK_A',)),
        threading.Timer(600.0, sim.coordinator.unfault_chamber, args=('BULK_A',)),
    ]
    for t in timers:
        t.daemon = True
        t.start()

    try:
        sim.run([merged])
    finally:
        for t in timers:
            t.cancel()


def scenario_4_e2e_rebalance():
    """场景4: 端到端全流程验收 (#05 unfault重平衡 + #06 E2E)
    2个Job各6片wafer（MergedJob, 共6个batch），BULK_A故障 t=230s，恢复 t=560s。
    - preheat≈90s；batch1(1000_1001)在t≈194s进入BULK_A，batch2(2000_2001)进入BULK_B
    - t=230s: 故障；1002_1003(PM_E pre-booked)重规划→PM_F；2002/2004后续也将路由到PM_F
    - t≈543s: 1002_1003/1004_1005到达TBS；1002_1003 queue[0]命中→立即发车到PM_F(busy=1)
               1004_1005 queue[0]是1002_1003 → is_next_step_ready=False → waiting_wafers
    - t=560s: unfault；PM_E释放frozen_task立即空闲；rebalance找到1004_1005 → 迁回PM_E（#05）
    - 故障持续330s；PM_E吞吐1000_1001+1004_1005，PM_F吞吐2000_2001+1002_1003+后续batch
    """
    logging.info("\n=== 场景4: 端到端全流程验收（#05+#06）===")

    sim = SchedulerSimulation()
    job1 = sim.create_job(job_id=1, sequence_type='basic', wafer_count=6)
    job2 = sim.create_job(job_id=2, sequence_type='basic', wafer_count=6)
    merged = merge_jobs(job1, job2)

    timers = [
        threading.Timer(230.0, sim.coordinator.fault_chamber,   args=('BULK_A',)),
        threading.Timer(560.0, sim.coordinator.unfault_chamber, args=('BULK_A',)),
    ]
    for t in timers:
        t.daemon = True
        t.start()

    try:
        sim.run([merged])
    finally:
        for t in timers:
            t.cancel()


# ================================================================
# 主函数
# ================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CVD 调度仿真器")
    parser.add_argument(
        '--scenario', type=int, default=1, choices=[1, 2, 3, 4],
        help='场景编号（1=单Job, 2=单Job+chamber故障, 3=联合故障验收, 4=端到端重平衡验收）'
    )
    args = parser.parse_args()

    scenarios = {
        1: scenario_1_single_job,
        2: scenario_2_fault_chamber_basic,
        3: scenario_3_fault_combined,
        4: scenario_4_e2e_rebalance,
    }
    scenarios[args.scenario]()
