# -*- coding: utf-8 -*-
# utils/location_utils.py - 位置ID解析工具
"""
位置ID解析工具
职责：纯字符串解析，无业务逻辑
"""
from typing import Tuple
from config import ConfigService


class LocationParser:
    """位置ID解析器 - 纯字符串处理"""

    @staticmethod
    def parse_location_id(location_id: str) -> Tuple[str, str, int]:
        """
        解析位置ID为组成部分
        
        Examples:
            "TREAT1_A_1" → ("TREAT1", "TREAT1_A", 1)
            "LL_A_2" → ("LL", "LL_A", 2)
            "FOUP_A_15" → ("FOUP", "FOUP_A", 15)
        
        Args:
            location_id: 位置ID字符串
            
        Returns:
            tuple: (模块类型, 基础模块ID, 层号)
        """
        parts = location_id.split('_')
        
        if len(parts) >= 3:
            module_type = parts[0]
            base_id = f"{parts[0]}_{parts[1]}"
            layer = int(parts[2])
        elif len(parts) == 2:
            module_type = parts[0]
            base_id = location_id
            layer = 1
        else:
            module_type = location_id
            base_id = location_id
            layer = 1
        
        return module_type, base_id, layer

    @staticmethod
    def get_base_module_id(location_id: str) -> str:
        """
        提取基础模块ID（去除层号）
        
        Examples:
            "TREAT1_A_1" → "TREAT1_A"
            "LL_A_2" → "LL_A"
            "FOUP_A_15" → "FOUP_A"
        
        Args:
            location_id: 完整位置ID
            
        Returns:
            str: 基础模块ID
        """
        _, base_id, _ = LocationParser.parse_location_id(location_id)
        return base_id

    @staticmethod
    def get_module_type(location_id: str) -> str:
        """
        提取模块类型
        
        Examples:
            "TREAT1_A_1" → "TREAT1"
            "LL_A_2" → "LL"
            "FOUP_A_15" → "FOUP"
        
        Args:
            location_id: 位置ID
            
        Returns:
            str: 模块类型
        """
        module_type, _, _ = LocationParser.parse_location_id(location_id)
        return module_type

    @staticmethod
    def get_layer(location_id: str) -> int:
        """
        提取层号
        
        Examples:
            "TREAT1_A_1" → 1
            "LL_A_2" → 2
        
        Args:
            location_id: 位置ID
            
        Returns:
            int: 层号
        """
        _, _, layer = LocationParser.parse_location_id(location_id)
        return layer

    @staticmethod
    def make_location_id(module_id: str, layer: int) -> str:
        """
        构造位置ID
        
        Examples:
            ("TREAT1_A", 1) → "TREAT1_A_1"
            ("LL_A", 2) → "LL_A_2"
        
        Args:
            module_id: 基础模块ID (如 "TREAT1_A")
            layer: 层号 (如 1)
            
        Returns:
            str: 完整位置ID (如 "TREAT1_A_1")
        """
        return f"{module_id}_{layer}"

    @staticmethod
    def get_z_level_for_location(location_id: str) -> str:
        """
        获取位置对应的Z轴高度
        
        Args:
            location_id: 位置ID
            
        Returns:
            str: Z轴高度 ('CHAMBER' 或 'LL_TBS')
        """

        base_module = LocationParser.get_base_module_id(location_id)
        
        # Chamber层
        if ConfigService.is_chamber_module(base_module):
            return "CHAMBER"
        
        # LL_TBS层：FOUP、ALIGNER、LL、TBS都在这一层
        return "LL_TBS"
