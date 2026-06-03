# -*- coding: utf-8 -*-
# domain/__init__.py
"""
领域服务层
包含跨模块的业务规则和领域逻辑
"""
from .resource_validator import ResourceValidator

__all__ = [
    'ResourceValidator',
]
