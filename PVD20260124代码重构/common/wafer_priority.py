# -*- coding: utf-8 -*-
# wafer_priority.py - Wafer优先级计算
import time
import math


class WaferPriorityCalculator:
    """Wafer优先级计算器"""

    def __init__(self, storage_weight=0.5, position_weight=0.5):
        self.storage_weight = storage_weight
        self.position_weight = position_weight

    def calculate_priority(self, wafer) -> float:
        """计算优先级 = 暂存时间因子*权重 + 工序位置因子*权重"""
        storage_factor = self._get_storage_factor(wafer)
        position_factor = self._get_position_factor(wafer)
        return storage_factor * self.storage_weight + position_factor * self.position_weight

    def _get_storage_factor(self, wafer) -> float:
        """暂存时间因子：时间越长分数越高"""
        if not hasattr(wafer, 'storage_start_time') or wafer.storage_start_time is None:
            return 0.0
        duration = time.time() - wafer.storage_start_time
        # sigmoid归一化：60秒时约0.5，300秒时约0.93
        return 1.0 / (1.0 + math.exp(-0.02 * (duration - 60)))

    def _get_position_factor(self, wafer) -> float:
        """工序位置因子：越靠后分数越高"""
        if not hasattr(wafer, 'current_step_index') or not hasattr(wafer, 'path_plan'):
            return 0.0
        total = len(wafer.path_plan)
        return wafer.current_step_index / total if total > 0 else 0.0