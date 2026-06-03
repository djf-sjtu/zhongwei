# -*- coding: utf-8 -*-
# config/system_config.py - 系统配置数据
"""
系统配置集中管理 - 纯配置数据
职责：只包含配置数据，无查询逻辑
"""


class SystemConfig:
    """系统配置类 - 纯配置数据"""

    # ================================================================
    # 仿真加速：所有时间参数统一乘以 TIME_SCALE
    # 改回 1.0 即恢复真实速度
    # ================================================================
    TIME_SCALE = 0.1

    # ================================================================
    # 显示名称配置
    # ================================================================
    CHAMBER_DISPLAY_NAMES = {
        'NUC_A': 'PM A',
        'NUC_B': 'PM C',
        'BULK_A': 'PM E',
        'BULK_B': 'PM F',
        'BULK_C': 'PM G'
    }

    # ================================================================
    # 模块配置
    # ================================================================

    # Chamber配置（BULK_C 暂时注释，减少 BULK 数量以触发 TEMP_PARK 场景）
    CHAMBER_MODULES = {
        'NUC_A': {'zone': 'TMA', 'process_type': 'NUC'},
        'NUC_B': {'zone': 'TMA', 'process_type': 'NUC'},
        'BULK_A': {'zone': 'TMB', 'process_type': 'BULK'},
        'BULK_B': {'zone': 'TMB', 'process_type': 'BULK'},
        'BULK_C': {'zone': 'TMB', 'process_type': 'BULK'},  # 暂时注释
    }

    # LL配置
    # 与PVD平台不同的是。LL是左右两个槽，而不是上下。
    # 为了代码兼容性，使用'layers'表示槽位数量（虽然物理上是左右slots）
    LL_MODULES = {
        'LL_A': {'layers': 2, 'cooling': False, 'type': 'in'},  # 进片槽
        'LL_C': {'layers': 2, 'cooling': True, 'type': 'out'},  # 出片槽
        'LL_B': {'layers': 2, 'cooling': False, 'type': 'in'},  # 进片槽
        'LL_D': {'layers': 2, 'cooling': True, 'type': 'out'},  # 出片槽
    }

    # TBS配置
    # 与PVD平台不同的是。TBS是左右两个槽，而不是上下。
    # 为了代码兼容性，使用'layers'表示槽位数量（虽然物理上是左右slots）
    TBS_MODULES = {
        'TBS_A': {'layers': 2,  'angle': 1},
        'TBS_B': {'layers': 2, 'angle': 1},
        'TBS_C': {'layers': 2,  'angle': 1},
        'TBS_D': {'layers': 2, 'angle': 1},
    }

    # FOUP配置
    FOUP_MODULES = {
        'FOUP_A': {'slots': 25},
        'FOUP_B': {'slots': 25},
        'FOUP_C': {'slots': 25},
    }

    # ALIGNER配置
    ALIGNER_MODULES = {
        'ALIGNER_A': {'angle': 1},
        'ALIGNER_B': {'angle': 1},
    }

    # 机械臂配置
    ROBOT_MODULES = {
        'EFEM': {'type': 'EFEM', 'arms': ['arm_1', 'arm_2']},
        'TMA': {'type': 'TMA', 'arms': ['arm_1', 'arm_2']},
        'TMB': {'type': 'TMB', 'arms': ['arm_1', 'arm_2']},
    }

    # ================================================================
    # 工艺时间配置
    # ================================================================

    PROCESS_TIMES = {
        'NUC': 900,
        'BULK': 3600,
        'LL': 8.5,
        'TBS': 0,
        'FOUP': 0,
        'ALIGNER': 5.0,
    }

    PROCESS_FILM_CONSUMPTION = {
        'NUC': 1,
        'BULK': 1,
        'LL': 0,
        'TBS': 0,
        'FOUP': 0,
        'ALIGNER': 0,
    }

    # ================================================================
    # 冷却和对准配置
    # ================================================================

    COOLING_TIME_SECONDS = 10.0

    ALIGNER_CONFIG = {
        'process_time_seconds': 5.0,
    }

    # Dry Pump配置
    DRY_PUMP_CONFIG = {
        'pump_time_seconds': 8.5,  # 抽真空时间（秒）
        'vent_time_seconds': 8.5
    }

    # ================================================================
    # 机械臂动作时间配置
    # ================================================================

    Z_MOVE_TIME = 2.0 * TIME_SCALE
    PICK_PLACE_TIME = 5.0 * TIME_SCALE
    TRANSPORT_BASE_TIME = 3.0 * TIME_SCALE

    # ================================================================
    # 调度算法配置
    # ================================================================

    SCHEDULING_CONFIG = {
        'chamber_storage_warning_threshold': 120.0 * TIME_SCALE,  # Chamber暂存报警阈值（秒）
        'periodic_check_interval': 1.0,  # 周期性检查间隔（秒，不缩放）
        'scheduling_cycle_interval_seconds': 2.0,  # 调度周期间隔（秒，不缩放）
        'max_retry_attempts': 3,
        'default_cleaning_duration_hours': 2.0,
    }

    # ================================================================
    # Chamber默认配置
    # ================================================================

    CHAMBER_DEFAULT_CONFIG = {
        'film_thickness_threshold': 70,
        'cleaning_duration_minutes': 20,
        'idle_macro_trigger_duration_seconds': 1200,
    }

    # ================================================================
    # 工艺序列模板
    # ================================================================

    COMMON_PROCESS_SEQUENCES = {
        'basic_flow': [
            'FOUP', 'ALIGNER', 'LL', 'NUC', 'TBS', 'BULK', 'TBS', 'LL', 'FOUP'
        ]
    }

    SWITCH_ADVANCE_TIME = 20.0 * TIME_SCALE