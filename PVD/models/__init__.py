# -*- coding: utf-8 -*-
# models/__init__.py
"""
数据模型层
导出所有领域模型和枚举
"""
from .enums import (
    ModuleID,
    WaferAngle,
    SlotID,
    str_to_module_id,
    module_id_to_str
)

from .domain_models import (
    RecipeView,
    Recipe,
    MacroStep,
    Macro,
    ScheduledMacro,
    SequenceStep,
    Sequence,
    Wafer,
    ProcessJob,
    Chamber,
    FOUP,
    LoadLock,
    TBS,
    Aligner,
    CSystem
)

__all__ = [
    # 枚举
    'ModuleID',
    'WaferAngle',
    'SlotID',
    'str_to_module_id',
    'module_id_to_str',
    
    # Recipe相关
    'RecipeView',
    'Recipe',
    
    # Macro相关
    'MacroStep',
    'Macro',
    'ScheduledMacro',
    
    # Sequence相关
    'SequenceStep',
    'Sequence',
    
    # 核心实体
    'Wafer',
    'ProcessJob',
    'Chamber',
    
    # 辅助设备
    'FOUP',
    'LoadLock',
    'TBS',
    'Aligner',
    
    # 系统容器
    'CSystem',
]
