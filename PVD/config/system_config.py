# -*- coding: utf-8 -*-
# config/system_config.py - 系统配置数据
"""
系统配置集中管理 - 纯配置数据
职责：只包含配置数据，无查询逻辑
"""


class SystemConfig:
    """系统配置类 - 纯配置数据"""

    # ================================================================
    # 仿真加速：业务时间和时间阈值统一乘以 TIME_SCALE
    # 仅用于缩短 wall-clock 仿真耗时，不影响调度决策的相对关系。
    # 改回 1.0 即恢复原速。范围由本文件末尾的白名单显式声明。
    # ================================================================
    TIME_SCALE = 1.0

    # ================================================================
    # 显示名称配置
    # ================================================================
    CHAMBER_DISPLAY_NAMES = {
        'TREAT1_A': 'PM G',
        'TREAT1_B': 'PM H',
        'TREAT2_A': 'PM E',
        'TREAT2_B': 'PM F',
        'ALD_A': 'PM C',
        'ALD_B': 'PM D',
        'PVD1_A': 'PM 1',
        'PVD1_B': 'PM 8',
        'CVD1_A': 'PM 2',
        'CVD1_B': 'PM 7',
        'PVD2_A': 'PM 3',
        'PVD2_B': 'PM 6',
        'CVD2_A': 'PM 4',
        'CVD2_B': 'PM 5',
    }

    # ================================================================
    # 模块配置
    # ================================================================
    
    # Chamber配置
    CHAMBER_MODULES = {
        'TREAT1_A': {'zone': 'TMA', 'process_type': 'TREAT1'},
        'TREAT1_B': {'zone': 'TMA', 'process_type': 'TREAT1'},
        'TREAT2_A': {'zone': 'TMA', 'process_type': 'TREAT2'},
        'TREAT2_B': {'zone': 'TMA', 'process_type': 'TREAT2'},
        'ALD_A': {'zone': 'TMA', 'process_type': 'ALD'},
        'ALD_B': {'zone': 'TMA', 'process_type': 'ALD'},
        'PVD1_A': {'zone': 'TMB', 'process_type': 'PVD1'},
        'PVD1_B': {'zone': 'TMB', 'process_type': 'PVD1'},
        'CVD1_A': {'zone': 'TMB', 'process_type': 'CVD1'},
        'CVD1_B': {'zone': 'TMB', 'process_type': 'CVD1'},
        'PVD2_A': {'zone': 'TMB', 'process_type': 'PVD2'},
        'PVD2_B': {'zone': 'TMB', 'process_type': 'PVD2'},
        'CVD2_A': {'zone': 'TMB', 'process_type': 'CVD2'},
        'CVD2_B': {'zone': 'TMB', 'process_type': 'CVD2'},
    }

    # LL配置
    LL_MODULES = {
        'LL_A': {'layers': 2, 'cooling': False, 'type': 'in'},   # 进片槽
        'LL_B': {'layers': 2, 'cooling': False, 'type': 'out'},  # 出片槽
    }

    # TBS配置
    TBS_MODULES = {
        'TBS_A': {'layers': 2, 'cooling': False, 'angle': 1},
        'TBS_B': {'layers': 2, 'cooling': False, 'angle': 1},
        'TBS_C': {'layers': 1, 'cooling': True, 'angle': 1},
        'TBS_D': {'layers': 1, 'cooling': True, 'angle': 1},
    }

    # FOUP配置
    FOUP_MODULES = {
        'FOUP_A': {'slots': 25},
        'FOUP_B': {'slots': 25},
        'FOUP_C': {'slots': 25},
        'FOUP_D': {'slots': 25},
    }

    # ALIGNER配置
    ALIGNER_MODULES = {
        'ALIGNER_A': {'angle': 1}
    }

    # 机械臂配置
    ROBOT_MODULES = {
        'EFEM': {'type': 'EFEM', 'arms': ['arm_1', 'arm_2']},
        'TMA': {'type': 'TMA', 'arms': ['arm_1', 'arm_2']},
        'TMB': {'type': 'TMB', 'arms': ['arm_1', 'arm_2']},
    }

    # 机械臂区域调度规则：from_zone → {to_zone: robot_id}
    # 缺失的组合返回 ''（原 RobotSelector 行为：未匹配的 LL→LL / LL→TMB / TBS→TBS 等）
    # 注意：from_zone='EFEM' 或 to_zone='EFEM' 一律使用 'EFEM'，已在表里展开。
    ROBOT_ZONE_RULES = {
        'EFEM': {'EFEM': 'EFEM', 'TMA': 'EFEM', 'TMB': 'EFEM', 'LL': 'EFEM', 'TBS': 'EFEM'},
        'TMA':  {'EFEM': 'EFEM', 'TMA': 'TMA',  'TMB': 'TMA',  'LL': 'TMA',  'TBS': 'TMA'},
        'TMB':  {'EFEM': 'EFEM', 'TMA': 'TMB',  'TMB': 'TMB',  'LL': 'TMB',  'TBS': 'TMB'},
        'LL':   {'EFEM': 'EFEM', 'TMA': 'TMA',                                'TBS': 'TMA'},
        'TBS':  {'EFEM': 'EFEM', 'TMA': 'TMA',  'TMB': 'TMB',  'LL': 'TMA'              },
    }

    # ================================================================
    # 工艺时间配置
    # ================================================================

    PROCESS_TIMES = {
        'TREAT1': 90.0,
        'TREAT2': 258.0,
        'ALD': 198.0,
        'PVD1': 64.0,
        'CVD1': 468.0,
        'PVD2': 604.0,
        'CVD2': 318.0,
        'LL': 0,
        'TBS': 0,
        'FOUP': 0,
        'ALIGNER': 5.0,
    }

    PROCESS_FILM_CONSUMPTION = {
        'TREAT1': 1,
        'TREAT2': 1,
        'ALD': 1,
        'PVD1': 1,
        'CVD1': 1,
        'PVD2': 1,
        'CVD2': 1,
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

    Z_MOVE_TIME = 2.0
    PICK_PLACE_TIME = 5.0
    TRANSPORT_BASE_TIME = 3.0

    # ================================================================
    # 调度算法配置
    # ================================================================

    SCHEDULING_CONFIG = {
        'chamber_storage_warning_threshold': 120.0,  # Chamber暂存报警阈值（秒）
        'periodic_check_interval': 1.0,              # 周期性检查间隔（秒）
        'scheduling_cycle_interval_seconds': 2.0,    # 调度周期间隔（秒）
        'max_retry_attempts': 3,
        'cleaning_check_interval_seconds': 10.0,
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
        'complex_flow': [
            'FOUP', 'ALIGNER', 'LL', 'TREAT1', 'TREAT2', 'ALD', 'TBS',
            'PVD1', 'CVD1', 'PVD2', 'CVD2', 'TBS', 'LL', 'FOUP'
        ],
        'simple_flow': [
            'FOUP', 'ALIGNER', 'LL', 'TREAT1', 'TBS', 'CVD1', 'PVD1', 'TBS', 'LL', 'FOUP'
        ],
        'basic_flow': [
            'FOUP', 'ALIGNER', 'LL', 'ALD', 'TBS', 'PVD2', 'CVD2', 'TBS', 'LL', 'FOUP'
        ]
    }


# ================================================================
# 加载时缩放：把白名单内的"时间/时间阈值"字段统一乘以 TIME_SCALE
# 任何新增的时间字段必须显式登记到此处，否则不会被缩放。
# 非时间字段（计数、长度、膜厚阈值等）绝不能登记。
# ================================================================

# 顶层标量时间字段
_SCALED_SCALAR_FIELDS = [
    'COOLING_TIME_SECONDS',
    'Z_MOVE_TIME',
    'PICK_PLACE_TIME',
    'TRANSPORT_BASE_TIME',
]

# 字典字段中的时间键：dict_attr -> [key, ...]；
# 值为 None 表示对整个 dict 的所有 value 缩放（用于 PROCESS_TIMES）
_SCALED_DICT_FIELDS = {
    'PROCESS_TIMES': None,
    'ALIGNER_CONFIG': ['process_time_seconds'],
    'DRY_PUMP_CONFIG': ['pump_time_seconds', 'vent_time_seconds'],
    'SCHEDULING_CONFIG': [
        'chamber_storage_warning_threshold',
        'periodic_check_interval',
        'scheduling_cycle_interval_seconds',
        'cleaning_check_interval_seconds',
        'default_cleaning_duration_hours',
    ],
    'CHAMBER_DEFAULT_CONFIG': [
        'idle_macro_trigger_duration_seconds',
        'cleaning_duration_minutes',
    ],
}


def _apply_time_scale():
    scale = SystemConfig.TIME_SCALE
    if scale == 1.0:
        return

    for field in _SCALED_SCALAR_FIELDS:
        current = getattr(SystemConfig, field)
        setattr(SystemConfig, field, current * scale)

    for attr, keys in _SCALED_DICT_FIELDS.items():
        d = getattr(SystemConfig, attr)
        if keys is None:
            for k, v in list(d.items()):
                d[k] = v * scale
        else:
            for k in keys:
                d[k] = d[k] * scale


_apply_time_scale()
