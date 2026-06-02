# -*- coding: utf-8 -*-
# services/execution/macro_manager.py
"""
宏任务管理器
职责：Job级别的宏任务编排、膜厚检查、Chamber管理
"""
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

from models import ProcessJob, Recipe, Macro, module_id_to_str
from entities import SchedulingChamber
from config import ConfigService
from common import LogIcon


@dataclass
class MacroTask:
    """宏任务数据结构"""
    task_id: str
    task_type: str  # 'prelot', 'postlot', 'cleaning', 'preheat'
    target_chamber_id: str
    recipe: Optional[Recipe] = None
    macro: Optional[Macro] = None
    duration: float = 0.0


class MacroManager:
    """宏任务管理器 - 负责Job级别的宏任务编排"""

    def __init__(self, system, task_executor):
        self.system = system
        self.task_executor = task_executor

    # ================================================================
    # 膜厚检查
    # ================================================================

    def check_film_thickness_sufficient(self, job: ProcessJob) -> bool:
        """检查膜厚是否足够支持job"""
        required = self._calculate_required_film(job)

        for process_type, required_total in required.items():
            available = self._get_available_film(process_type)
            if available < required_total:
                logging.warning(f"{LogIcon.WARNING} 工艺 {process_type} 膜厚不足: "
                               f"需要{required_total}, 可用{available}")
                return False
        return True

    def _calculate_required_film(self, job: ProcessJob) -> Dict[str, int]:
        """计算job需要的总膜厚"""
        required_film = {}
        sequence = job.sequence_for_job
        wafer_count = len(job.wafer_collection)

        for step in sequence.sequence_steps:
            if not step.target_modules:
                continue

            recipe = step.process_recipe

            for module_id in step.target_modules:
                module_str = module_id_to_str(module_id)

                if not ConfigService.is_chamber_module(module_str):
                    continue

                process_type = ConfigService.get_process_type(module_str)
                film_consumption = ConfigService.calculate_film_consumption_from_recipe(
                    recipe, module_str
                )

                if process_type not in required_film:
                    required_film[process_type] = 0
                required_film[process_type] += film_consumption * wafer_count

        return required_film

    def _get_available_film(self, process_type: str) -> int:
        """获取某工艺类型所有chamber的总可用膜厚"""
        total = 0

        for chamber_id, chamber in self.system.chambers.items():
            chamber_process = ConfigService.get_process_type(chamber_id)
            if chamber_process == process_type:
                available = (chamber.base.film_thickness_threshold_to_trigger_dry_clean -
                           chamber.base.film_thickness_counter)
                total += available

        return total

    # ================================================================
    # Chamber查询
    # ================================================================

    def get_target_chambers(self, job: ProcessJob) -> List[SchedulingChamber]:
        """获取job需要的所有chambers"""
        chambers = []
        seen_ids = set()

        for step in job.sequence_for_job.sequence_steps:
            if not step.target_modules:
                continue

            for module_id in step.target_modules:
                module_str = module_id_to_str(module_id)

                if not ConfigService.is_chamber_module(module_str):
                    continue

                location_id = f"{module_str}_1"

                if location_id not in seen_ids:
                    chamber = self.system.chambers.get(location_id)
                    if chamber:
                        chambers.append(chamber)
                        seen_ids.add(location_id)

        return chambers

    def get_unused_chambers(self, job: ProcessJob) -> List[SchedulingChamber]:
        """获取job不使用的chambers"""
        used_ids = set()

        for step in job.sequence_for_job.sequence_steps:
            if not step.target_modules:
                continue

            for module_id in step.target_modules:
                module_str = module_id_to_str(module_id)
                if ConfigService.is_chamber_module(module_str):
                    used_ids.add(f"{module_str}_1")

        return [c for cid, c in self.system.chambers.items() if cid not in used_ids]

    def get_chambers_from_recipe(self, recipe: Recipe) -> List[SchedulingChamber]:
        """从recipe提取相关chambers"""
        chambers = []
        seen_ids = set()

        for recipe_view in recipe.recipe_views:
            module_str = module_id_to_str(recipe_view.module_id)
            if ConfigService.is_chamber_module(module_str):
                location_id = f"{module_str}_1"
                if location_id not in seen_ids:
                    chamber = self.system.chambers.get(location_id)
                    if chamber:
                        chambers.append(chamber)
                        seen_ids.add(location_id)

        return chambers

    # ================================================================
    # 宏任务创建
    # ================================================================

    def create_prelot_task(self, recipe: Recipe, chamber: SchedulingChamber) -> MacroTask:
        """创建prelot任务"""
        return MacroTask(
            task_id=self._generate_task_id("prelot", chamber.location_id),
            task_type='prelot',
            target_chamber_id=chamber.location_id,
            recipe=recipe,
            duration=self._get_recipe_duration(recipe, chamber)
        )

    def create_postlot_task(self, recipe: Recipe, chamber: SchedulingChamber) -> MacroTask:
        """创建postlot任务"""
        return MacroTask(
            task_id=self._generate_task_id("postlot", chamber.location_id),
            task_type='postlot',
            target_chamber_id=chamber.location_id,
            recipe=recipe,
            duration=self._get_recipe_duration(recipe, chamber)
        )

    def create_preheat_task(self, chamber: SchedulingChamber) -> Optional[MacroTask]:
        """创建预热任务"""
        if not chamber.base.idle_macro:
            return None

        process_type = ConfigService.get_process_type(module_id_to_str(chamber.module_id))
        duration = ConfigService.get_process_time(process_type, 60.0)

        return MacroTask(
            task_id=self._generate_task_id("preheat", chamber.location_id),
            task_type='preheat',
            target_chamber_id=chamber.location_id,
            macro=chamber.base.idle_macro,
            duration=duration
        )

    def create_cleaning_task(self, chamber: SchedulingChamber) -> MacroTask:
        """创建清洗任务"""
        duration = ConfigService.get_cleaning_duration_minutes() * 60

        return MacroTask(
            task_id=self._generate_task_id("cleaning", chamber.location_id),
            task_type='cleaning',
            target_chamber_id=chamber.location_id,
            duration=duration
        )

    def _generate_task_id(self, task_type: str, identifier: str) -> str:
        """生成唯一任务ID"""
        return f"{task_type}_{identifier}_{int(time.time() * 1000)}"

    def _get_recipe_duration(self, recipe: Recipe, chamber: SchedulingChamber) -> float:
        """从recipe获取时长"""
        for recipe_view in recipe.recipe_views:
            if recipe_view.module_id == chamber.module_id:
                return recipe_view.recipe_max_time_seconds
        return 60.0

    # ================================================================
    # 宏任务安排
    # ================================================================

    def arrange_prelot(self, job: ProcessJob):
        """安排Prelot任务"""
        if not job.sequence_for_job.pre_lot_recipe:
            return

        chambers = self.get_chambers_from_recipe(job.sequence_for_job.pre_lot_recipe)

        for chamber in chambers:
            task = self.create_prelot_task(job.sequence_for_job.pre_lot_recipe, chamber)
            self.task_executor.push_macro_task(chamber, task)

    def arrange_postlot(self, job: ProcessJob):
        """安排Postlot任务"""
        if not job.sequence_for_job.post_lot_recipe:
            return

        chambers = self.get_chambers_from_recipe(job.sequence_for_job.post_lot_recipe)

        for chamber in chambers:
            task = self.create_postlot_task(job.sequence_for_job.post_lot_recipe, chamber)
            self.task_executor.push_macro_task(chamber, task)

    def arrange_preheat(self, job: ProcessJob):
        """安排预热任务"""
        chambers = self.get_target_chambers(job)
        
        for chamber in chambers:
            if chamber.needs_immediate_preheat():
                task = self.create_preheat_task(chamber)
                if task:
                    self.task_executor.push_macro_task(chamber, task)

    def arrange_cleaning(self, chamber: SchedulingChamber):
        """安排清洗任务"""
        task = self.create_cleaning_task(chamber)
        self.task_executor.push_macro_task(chamber, task)
