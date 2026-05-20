# -*- coding: utf-8 -*-
# services/scheduling/robot_selector.py - 机械臂选择服务
"""
机械臂选择服务
职责：根据源位置和目标位置选择合适的机械臂
"""
from config import ConfigService
from utils import LocationParser


class RobotSelector:
    """机械臂选择器"""

    @staticmethod
    def select_robot(from_location: str, to_location: str) -> str:
        """
        根据源和目标位置选择机械臂
        
        Args:
            from_location: 源位置ID
            to_location: 目标位置ID
            
        Returns:
            str: 机械臂ID ('EFEM', 'TMA', 'TMB')
        """
        # 获取位置所在的区域
        from_base = LocationParser.get_base_module_id(from_location)
        to_base = LocationParser.get_base_module_id(to_location)
        
        from_zone = ConfigService.get_module_zone(from_base)
        to_zone = ConfigService.get_module_zone(to_base)

        # EFEM区域：任何涉及EFEM的传输都用EFEM
        if from_zone == 'EFEM' or to_zone == 'EFEM':
            return 'EFEM'

        # LL区域
        if from_zone == 'LL':
            if to_zone in ['TMA', 'TBS']:
                return 'TMA'
            elif to_zone == 'EFEM':
                return 'EFEM'
            return ''

        # TBS区域
        if from_zone == 'TBS':
            if to_zone in ['TMA', 'LL']:
                return 'TMA'
            elif to_zone == 'TMB':
                return 'TMB'

        # TMA区域
        if from_zone == 'TMA':
            return 'TMA'

        # TMB区域
        if from_zone == 'TMB':
            return 'TMB'

        return ''
