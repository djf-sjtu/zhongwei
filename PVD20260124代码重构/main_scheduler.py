import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
# -*- coding: utf-8 -*-
# main_scheduler.py - 主调度系统入口
"""
半导体制造调度系统 - 主入口
使用重构后的模块化架构
"""
import time
import logging
import random
from models import (
    Wafer, ProcessJob,
    WaferAngle, SlotID, Sequence, SequenceStep, Recipe, RecipeView,
    ModuleID, module_id_to_str
)
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
        """创建测试Job"""
        sequence = self._create_sequence(f"test_sequence_{job_id}", sequence_type)
        job = ProcessJob(
            process_job_id=job_id,
            sequence_for_job=sequence,
            wafer_collection=[]
        )

        # 创建wafers
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
            job.wafer_collection.append(wafer)
            current_slot += 1

        logging.info(f"创建 Job {job_id} ({sequence_type}): {wafer_count} wafers, "
                     f"角度: {job_angle.value}")
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
        # sequence.pre_lot_recipe = self._create_lot_recipe(sequence, "prelot")
        # sequence.pre_lot_recipe_condition_chamber_idle_time = random.uniform(300, 600)
        #
        # sequence.post_lot_recipe = self._create_lot_recipe(sequence, "postlot")

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
            # 随机选择两个chambers
            selected_chambers = random.sample(job_chambers, min(2, len(job_chambers)))

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

        # 输出统计
        end_time = time.time()
        duration = end_time - start_time

        logging.info(f"\n{'=' * 80}")
        logging.info(f"{LogIcon.COMPLETE} 仿真完成")
        logging.info(f"{'=' * 80}")
        logging.info(f"总耗时: {duration:.1f}秒")
        logging.info(f"{'=' * 80}\n")


# ================================================================
# 测试场景
# ================================================================

def scenario_1_single_job():
    """场景1: 单个Job"""
    logging.info("\n=== 场景1: 单个Job（简单工艺） ===")
    
    sim = SchedulerSimulation()
    job = sim.create_job(job_id=1, sequence_type='complex', wafer_count=5)
    sim.run([job])


def scenario_2_sequential_jobs():
    """场景2: 两个顺序Job"""
    logging.info("\n=== 场景2: 两个顺序Job ===")
    
    sim = SchedulerSimulation()
    job1 = sim.create_job(job_id=1, sequence_type='simple', wafer_count=5)
    job2 = sim.create_job(job_id=2, sequence_type='basic', wafer_count=5)
    sim.run([job1, job2])


def scenario_3_merged_jobs():
    """场景3: 合并并行Job"""
    logging.info("\n=== 场景3: 合并并行Job ===")
    
    sim = SchedulerSimulation()
    job1 = sim.create_job(job_id=1, sequence_type='simple', wafer_count=5)
    job2 = sim.create_job(job_id=2, sequence_type='basic', wafer_count=5)
    #job3 = sim.create_job(job_id=3, sequence_type='complex', wafer_count=5)
    merged_job = merge_jobs(job1, job2)
    sim.run([merged_job])




# ================================================================
# 主函数
# ================================================================

if __name__ == "__main__":
    # 运行场景
    #scenario_1_single_job()
    # scenario_2_sequential_jobs()
    scenario_3_merged_jobs()
    # scenario_4_complex_flow()
