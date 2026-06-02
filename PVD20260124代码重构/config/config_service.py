# -*- coding: utf-8 -*-
# config/config_service.py - 配置查询服务
"""
配置查询服务
职责：提供统一的配置访问接口
"""
from typing import List, Optional, Dict, Any

from .system_config import SystemConfig


class ConfigService:
    """配置查询服务 - 统一的配置访问接口"""

    # ================================================================
    # 模块信息查询
    # ================================================================

    @staticmethod
    def get_process_type(module_id: str) -> str:
        """
        获取模块的工艺类型
        
        Args:
            module_id: 模块ID (如 "TREAT1_A")
            
        Returns:
            str: 工艺类型 (如 "TREAT1")
        """
        if module_id in SystemConfig.CHAMBER_MODULES:
            return SystemConfig.CHAMBER_MODULES[module_id]['process_type']
        
        # 提取前缀作为工艺类型
        return module_id.split('_')[0]

    @staticmethod
    def get_module_zone(module_id: str) -> str:
        """
        获取模块所在的机械臂区域
        
        Args:
            module_id: 模块ID
            
        Returns:
            str: 区域名称 ('EFEM', 'TMA', 'TMB', 'LL', 'TBS')
        """
        # Chamber
        if module_id in SystemConfig.CHAMBER_MODULES:
            return SystemConfig.CHAMBER_MODULES[module_id]['zone']
        
        # LL
        if module_id in SystemConfig.LL_MODULES:
            return 'LL'
        
        # TBS
        if module_id in SystemConfig.TBS_MODULES:
            return 'TBS'
        
        # 默认EFEM
        return 'EFEM'

    @staticmethod
    def is_chamber_module(module_id: str) -> bool:
        """判断是否为Chamber模块"""
        return module_id in SystemConfig.CHAMBER_MODULES

    @staticmethod
    def is_ll_module(module_id: str) -> bool:
        """判断是否为LL模块"""
        return module_id in SystemConfig.LL_MODULES

    @staticmethod
    def is_tbs_module(module_id: str) -> bool:
        """判断是否为TBS模块"""
        return module_id in SystemConfig.TBS_MODULES

    @staticmethod
    def is_foup_module(module_id: str) -> bool:
        """判断是否为FOUP模块"""
        return module_id in SystemConfig.FOUP_MODULES

    @staticmethod
    def is_aligner_module(module_id: str) -> bool:
        """判断是否为Aligner模块"""
        return module_id in SystemConfig.ALIGNER_MODULES

    # ================================================================
    # 机械臂区域调度
    # ================================================================

    @staticmethod
    def get_robot_for_transport(from_location: str, to_location: str) -> str:
        """
        根据源和目标位置选择搬运机械臂。
        逻辑：解析两端所在 zone → 查 ROBOT_ZONE_RULES 表 → 返回 robot_id。
        未匹配的组合返回 ''（保留原 RobotSelector 行为）。
        """
        # 延迟 import 规避 utils <-> config 循环
        from utils import LocationParser
        from_base = LocationParser.get_base_module_id(from_location)
        to_base = LocationParser.get_base_module_id(to_location)
        from_zone = ConfigService.get_module_zone(from_base)
        to_zone = ConfigService.get_module_zone(to_base)
        return SystemConfig.ROBOT_ZONE_RULES.get(from_zone, {}).get(to_zone, '')

    # ================================================================
    # LL相关查询
    # ================================================================

    @staticmethod
    def is_ll_in_slot(module_id: str) -> bool:
        """判断是否为LL进片槽"""
        if module_id in SystemConfig.LL_MODULES:
            return SystemConfig.LL_MODULES[module_id].get('type') == 'in'
        return False

    @staticmethod
    def is_ll_out_slot(module_id: str) -> bool:
        """判断是否为LL出片槽"""
        if module_id in SystemConfig.LL_MODULES:
            return SystemConfig.LL_MODULES[module_id].get('type') == 'out'
        return False

    # ================================================================
    # TBS相关查询
    # ================================================================

    @staticmethod
    def needs_cooling(module_id: str) -> bool:
        """判断TBS是否需要冷却"""
        if module_id in SystemConfig.TBS_MODULES:
            return SystemConfig.TBS_MODULES[module_id].get('cooling', False)
        return False

    @staticmethod
    def get_tbs_by_angle(angle: int, needs_cooling) -> List[str]:
        """
        根据角度和冷却需求获取TBS模块
        
        Args:
            angle: 角度值
            
        Returns:
            List[str]: 匹配的TBS模块ID列表
        """
        candidates = []
        for tbs_id, config in SystemConfig.TBS_MODULES.items():
            if config['angle'] == angle and config['cooling'] == needs_cooling:
                candidates.append(tbs_id)
        return candidates

    # ================================================================
    # 工艺时间查询
    # ================================================================

    @staticmethod
    def get_process_time(process_type: str, default: float = 60.0) -> float:
        """
        获取工艺处理时间
        
        Args:
            process_type: 工艺类型
            default: 默认时间
            
        Returns:
            float: 处理时间（秒）
        """
        return SystemConfig.PROCESS_TIMES.get(process_type, default)

    @staticmethod
    def get_film_consumption(process_type: str) -> int:
        """
        获取膜厚消耗

        Args:
            process_type: 工艺类型

        Returns:
            int: 膜厚消耗值
        """
        return SystemConfig.PROCESS_FILM_CONSUMPTION.get(process_type, 1)

    @staticmethod
    def calculate_film_consumption_from_recipe(recipe, target_module_id) -> int:
        """
        从 Recipe 查特定模块的膜厚消耗；找不到时按工艺类型回退到默认值。
        target_module_id 接受 ModuleID 或字符串。
        """
        from models import module_id_to_str
        target_module_str = (
            module_id_to_str(target_module_id)
            if not isinstance(target_module_id, str)
            else target_module_id
        )

        for recipe_view in recipe.recipe_views:
            if module_id_to_str(recipe_view.module_id) == target_module_str:
                return recipe_view.film_thickness_on_station[0]

        # Recipe 中没有找到，使用配置默认值
        process_type = ConfigService.get_process_type(target_module_str)
        return ConfigService.get_film_consumption(process_type)

    # ================================================================
    # 模块列表查询
    # ================================================================

    @staticmethod
    def get_modules_by_process_type(process_type: str) -> List[str]:
        """
        根据工艺类型获取所有相关模块
        
        Args:
            process_type: 工艺类型
            
        Returns:
            List[str]: 模块ID列表
        """
        # Chamber
        chamber_modules = [
            module_id for module_id, config in SystemConfig.CHAMBER_MODULES.items()
            if config['process_type'] == process_type
        ]
        if chamber_modules:
            return chamber_modules
        
        # LL
        if process_type == 'LL':
            return list(SystemConfig.LL_MODULES.keys())
        
        # TBS
        if process_type == 'TBS':
            return list(SystemConfig.TBS_MODULES.keys())
        
        # ALIGNER
        if process_type == 'ALIGNER':
            return list(SystemConfig.ALIGNER_MODULES.keys())
        
        # FOUP
        if process_type == 'FOUP':
            return list(SystemConfig.FOUP_MODULES.keys())
        
        return []

    @staticmethod
    def get_all_chamber_modules() -> List[str]:
        """获取所有Chamber模块ID"""
        return list(SystemConfig.CHAMBER_MODULES.keys())

    @staticmethod
    def get_all_ll_modules() -> List[str]:
        """获取所有LL模块ID"""
        return list(SystemConfig.LL_MODULES.keys())

    @staticmethod
    def get_all_tbs_modules() -> List[str]:
        """获取所有TBS模块ID"""
        return list(SystemConfig.TBS_MODULES.keys())

    # ================================================================
    # 时间配置查询
    # ================================================================

    @staticmethod
    def get_cooling_time() -> float:
        """获取冷却时间"""
        return SystemConfig.COOLING_TIME_SECONDS

    @staticmethod
    def get_aligner_process_time() -> float:
        """获取Aligner处理时间"""
        return SystemConfig.ALIGNER_CONFIG['process_time_seconds']

    @staticmethod
    def get_dry_pump_time() -> float:
        """获取抽真空时间"""
        return SystemConfig.DRY_PUMP_CONFIG['pump_time_seconds']

    @staticmethod
    def get_vent_time() -> float:
        """获取破真空时间"""
        return SystemConfig.DRY_PUMP_CONFIG['vent_time_seconds']

    @staticmethod
    def get_z_move_time() -> float:
        """获取Z轴移动时间"""
        return SystemConfig.Z_MOVE_TIME

    @staticmethod
    def get_pick_place_time() -> float:
        """获取取放片时间"""
        return SystemConfig.PICK_PLACE_TIME

    @staticmethod
    def get_transport_base_time() -> float:
        """获取基础传输时间"""
        return SystemConfig.TRANSPORT_BASE_TIME

    # ================================================================
    # Chamber配置查询
    # ================================================================

    @staticmethod
    def get_chamber_film_threshold() -> int:
        """获取Chamber膜厚阈值"""
        return SystemConfig.CHAMBER_DEFAULT_CONFIG['film_thickness_threshold']

    @staticmethod
    def get_cleaning_duration_minutes() -> int:
        """获取清洗时长（分钟）"""
        return SystemConfig.CHAMBER_DEFAULT_CONFIG['cleaning_duration_minutes']

    @staticmethod
    def get_idle_macro_trigger_duration() -> float:
        """获取空闲宏任务触发时长"""
        return SystemConfig.CHAMBER_DEFAULT_CONFIG['idle_macro_trigger_duration_seconds']

    # ================================================================
    # 调度配置查询
    # ================================================================

    @staticmethod
    def get_scheduling_config(key: str, default: Any = None) -> Any:
        """
        获取调度配置项
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            配置值
        """
        return SystemConfig.SCHEDULING_CONFIG.get(key, default)

    # ================================================================
    # 显示名称查询
    # ================================================================

    @staticmethod
    def get_display_name(module_id: str) -> str:
        """
        获取模块的显示名称
        
        Args:
            module_id: 模块ID
            
        Returns:
            str: 显示名称（如果存在），否则返回原ID
        """
        return SystemConfig.CHAMBER_DISPLAY_NAMES.get(module_id, module_id)
