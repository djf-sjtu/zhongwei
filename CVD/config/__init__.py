# -*- coding: utf-8 -*-
# config/__init__.py
"""
配置层
提供统一的配置访问接口
"""
from .system_config import SystemConfig
from .config_service import ConfigService

__all__ = [
    'SystemConfig',
    'ConfigService',
]
