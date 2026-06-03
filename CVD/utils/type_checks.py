# -*- coding: utf-8 -*-
# utils/type_checks.py - 类型检查工具
"""
类型检查工具
职责：简单的类型判断，无业务逻辑
"""
from config import ConfigService
from utils.location_utils import LocationParser


def is_foup(location_id: str) -> bool:
    """
    判断是否为FOUP位置
    
    Args:
        location_id: 位置ID
        
    Returns:
        bool: 是否为FOUP
    """
    base_module = LocationParser.get_base_module_id(location_id)
    return ConfigService.is_foup_module(base_module)


def is_ll(location_id: str) -> bool:
    """
    判断是否为LL位置
    
    Args:
        location_id: 位置ID
        
    Returns:
        bool: 是否为LL
    """
    base_module = LocationParser.get_base_module_id(location_id)
    return ConfigService.is_ll_module(base_module)


def is_tbs(location_id: str) -> bool:
    """
    判断是否为TBS位置
    
    Args:
        location_id: 位置ID
        
    Returns:
        bool: 是否为TBS
    """
    base_module = LocationParser.get_base_module_id(location_id)
    return ConfigService.is_tbs_module(base_module)


def is_aligner(location_id: str) -> bool:
    """
    判断是否为ALIGNER位置
    
    Args:
        location_id: 位置ID
        
    Returns:
        bool: 是否为ALIGNER
    """
    base_module = LocationParser.get_base_module_id(location_id)
    return ConfigService.is_aligner_module(base_module)


def is_chamber(location_id: str) -> bool:
    """
    判断是否为Chamber位置
    
    Args:
        location_id: 位置ID
        
    Returns:
        bool: 是否为Chamber
    """
    base_module = LocationParser.get_base_module_id(location_id)
    return ConfigService.is_chamber_module(base_module)
