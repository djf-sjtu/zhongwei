# -*- coding: utf-8 -*-
# models/domain_models.py - 领域模型
"""
核心业务领域模型定义
职责：数据结构定义，无业务逻辑
"""
from typing import List, Optional
from dataclasses import dataclass, field

from .enums import ModuleID, WaferAngle, SlotID


# ================================================================
# Recipe相关数据结构
# ================================================================

@dataclass
class RecipeView:
    """Recipe视图"""
    module_id: ModuleID
    recipe_max_time_seconds: float
    recipe_step_count: int
    film_thickness_on_station: List[int] = field(default_factory=lambda: [0, 0])


@dataclass
class Recipe:
    """Recipe配方"""
    recipe_name: str
    recipe_views: List[RecipeView] = field(default_factory=list)


# ================================================================
# Macro相关数据结构
# ================================================================

@dataclass
class MacroStep:
    """Macro步骤"""
    step_id: int
    action_to_do: str


@dataclass
class Macro:
    """Macro宏"""
    macro_name: str
    steps: List[MacroStep] = field(default_factory=list)


@dataclass
class ScheduledMacro:
    """计划执行的Macro"""
    when: float
    macro_to_run: Macro


# ================================================================
# Sequence相关数据结构
# ================================================================

@dataclass
class SequenceStep:
    """工艺序列步骤"""
    step_id: int
    target_modules: List[ModuleID]
    process_recipe: Recipe
    post_clean_recipe: Optional[Recipe] = None
    cooling_time_for_tbs_or_ll: float = 0.0


@dataclass
class Sequence:
    """工艺序列"""
    sequence_name: str
    sequence_steps: List[SequenceStep] = field(default_factory=list)
    pre_lot_recipe: Optional[Recipe] = None
    pre_lot_recipe_condition_chamber_idle_time: float = 0.0
    post_lot_recipe: Optional[Recipe] = None
    interval_recipe: Optional[Recipe] = None
    interval_wafer_count: int = 0


# ================================================================
# 核心业务实体
# ================================================================

@dataclass
class Wafer:
    """晶圆"""
    wafer_id: int
    source_foup: ModuleID
    source_foup_slot: SlotID
    associated_sequence: Sequence
    angle: WaferAngle = WaferAngle.ANGLE_1


@dataclass
class ProcessJob:
    """生产任务"""
    process_job_id: int
    sequence_for_job: Sequence
    wafer_collection: List[Wafer] = field(default_factory=list)


@dataclass
class Chamber:
    """反应腔"""
    module_id: ModuleID
    online: int
    busy: int
    film_thickness_counter: int
    film_thickness_threshold_to_trigger_dry_clean: int
    idle_macro_trigger_duration: float
    idle_macro: Optional[Macro] = None
    scheduled_macro_collection: List[ScheduledMacro] = field(default_factory=list)


# ================================================================
# 辅助设备实体
# ================================================================

@dataclass
class FOUP:
    """前装盒"""
    module_id: ModuleID


@dataclass
class LoadLock:
    """Load Lock"""
    module_id: ModuleID
    max_slots: int = 2


@dataclass
class TBS:
    """Transfer Buffer Station"""
    module_id: ModuleID
    max_slots: int = 2


@dataclass
class Aligner:
    """对准器"""
    module_id: ModuleID
    max_slots: int = 1


# ================================================================
# 系统容器
# ================================================================

@dataclass
class CSystem:
    """系统容器"""
    chamber_collection: List[Chamber] = field(default_factory=list)
    load_lock_collection: List[LoadLock] = field(default_factory=list)
    tbs_collection: List[TBS] = field(default_factory=list)
    tm_collection: List = field(default_factory=list)
    #efem: Optional = None
    foup_collection: List[FOUP] = field(default_factory=list)
    wafers_in_system: List[Wafer] = field(default_factory=list)
    process_job_collection: List[ProcessJob] = field(default_factory=list)
