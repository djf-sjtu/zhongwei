# -*- coding: utf-8 -*-
# domain/wafer_service.py - Wafer领域服务
"""
Wafer领域服务
职责：Wafer相关的业务逻辑
"""
from models import module_id_to_str
from config import SystemConfig, ConfigService


class WaferService:
    """Wafer领域服务"""

    @staticmethod
    def calculate_film_consumption_from_recipe(recipe, target_module_id: str) -> int:
        """
        从Recipe中计算特定模块的膜厚消耗
        
        Args:
            recipe: Recipe对象
            target_module_id: 目标模块ID
            
        Returns:
            int: 膜厚消耗值
        """
        target_module_str = module_id_to_str(target_module_id) if not isinstance(
            target_module_id, str) else target_module_id

        for recipe_view in recipe.recipe_views:
            if module_id_to_str(recipe_view.module_id) == target_module_str:
                return recipe_view.film_thickness_on_station[0]

        # 如果Recipe中没有找到，使用配置默认值
        process_type = ConfigService.get_process_type(target_module_str)
        return ConfigService.get_film_consumption(process_type)

    @staticmethod
    def get_foup_location_id(wafer) -> str:
        """
        获取wafer的FOUP位置ID
        
        Args:
            wafer: SchedulingWafer对象
            
        Returns:
            str: FOUP位置ID (如 "FOUP_A_15")
        """
        foup_str = module_id_to_str(wafer.base.source_foup)
        slot_value = wafer.base.source_foup_slot.value
        return f"{foup_str}_{slot_value}"

    @staticmethod
    def get_wafer_angle(wafer) -> int:
        """
        获取wafer的角度值
        
        Args:
            wafer: SchedulingWafer对象
            
        Returns:
            int: 角度值
        """
        return wafer.base.angle.value

    @staticmethod
    def has_remaining_steps(wafer) -> bool:
        """
        检查wafer是否还有剩余工艺步骤
        
        Args:
            wafer: SchedulingWafer对象
            
        Returns:
            bool: 是否有剩余步骤
        """
        return not wafer.is_sequence_completed()
