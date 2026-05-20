# -*- coding: utf-8 -*-
# models/enums.py - 枚举定义
"""
系统枚举类型定义
职责：纯枚举，无业务逻辑
"""
from enum import Enum


# ================================================================
# 模块ID枚举
# ================================================================

class ModuleID(Enum):
    """模块ID枚举 - 字符串枚举"""
    
    # Chamber模块
    TREAT1_A = "TREAT1_A"
    TREAT1_B = "TREAT1_B"
    TREAT2_A = "TREAT2_A"
    TREAT2_B = "TREAT2_B"
    ALD_A = "ALD_A"
    ALD_B = "ALD_B"
    PVD1_A = "PVD1_A"
    PVD1_B = "PVD1_B"
    CVD1_A = "CVD1_A"
    CVD1_B = "CVD1_B"
    PVD2_A = "PVD2_A"
    PVD2_B = "PVD2_B"
    CVD2_A = "CVD2_A"
    CVD2_B = "CVD2_B"

    # LoadLock模块
    LL_A = "LL_A"
    LL_B = "LL_B"

    # TBS模块
    TBS_A = "TBS_A"
    TBS_B = "TBS_B"
    TBS_C = "TBS_C"
    TBS_D = "TBS_D"

    # FOUP模块
    FOUP_A = "FOUP_A"
    FOUP_B = "FOUP_B"
    FOUP_C = "FOUP_C"
    FOUP_D = "FOUP_D"

    # Aligner模块
    ALIGNER_A = "ALIGNER_A"
    ALIGNER_B = "ALIGNER_B"

    # Robot模块
    EFEM = "EFEM"
    TMA = "TMA"
    TMB = "TMB"


class WaferAngle(Enum):
    """Wafer角度枚举"""
    ANGLE_1 = 1
    ANGLE_2 = 2


class SlotID(Enum):
    """槽位ID枚举 - FOUP最多25个槽位"""
    SLOT_1 = 1
    SLOT_2 = 2
    SLOT_3 = 3
    SLOT_4 = 4
    SLOT_5 = 5
    SLOT_6 = 6
    SLOT_7 = 7
    SLOT_8 = 8
    SLOT_9 = 9
    SLOT_10 = 10
    SLOT_11 = 11
    SLOT_12 = 12
    SLOT_13 = 13
    SLOT_14 = 14
    SLOT_15 = 15
    SLOT_16 = 16
    SLOT_17 = 17
    SLOT_18 = 18
    SLOT_19 = 19
    SLOT_20 = 20
    SLOT_21 = 21
    SLOT_22 = 22
    SLOT_23 = 23
    SLOT_24 = 24
    SLOT_25 = 25


# ================================================================
# 辅助函数
# ================================================================

def str_to_module_id(module_str: str) -> ModuleID:
    """
    将字符串转换为ModuleID枚举
    
    Args:
        module_str: 模块ID字符串
        
    Returns:
        ModuleID: 对应的枚举值
        
    Raises:
        ValueError: 无效的模块ID
    """
    try:
        return ModuleID(module_str)
    except KeyError:
        raise ValueError(f"Unknown module ID: {module_str}")


def module_id_to_str(module_id) -> str:
    """
    将ModuleID枚举转换为字符串
    
    Args:
        module_id: ModuleID枚举或字符串
        
    Returns:
        str: 模块ID字符串
    """
    if isinstance(module_id, str):
        return module_id
    return module_id.value
