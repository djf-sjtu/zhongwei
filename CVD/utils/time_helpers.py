# -*- coding: utf-8 -*-
# utils/time_helpers.py - 时间辅助工具
"""
时间计算辅助工具
职责：简单的时间计算，无复杂业务逻辑
"""
import time


class TimeHelper:
    """时间辅助工具"""

    @staticmethod
    def get_current_time() -> float:
        """
        获取当前时间戳
        
        Returns:
            float: 当前时间戳（秒）
        """
        return time.time()

    @staticmethod
    def calculate_duration(start_time: float, end_time: float) -> float:
        """
        计算时间间隔
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            
        Returns:
            float: 时间间隔（秒）
        """
        return end_time - start_time

    @staticmethod
    def has_exceeded_threshold(start_time: float, threshold: float) -> bool:
        """
        检查是否超过时间阈值
        
        Args:
            start_time: 开始时间
            threshold: 阈值（秒）
            
        Returns:
            bool: 是否超过阈值
        """
        if start_time is None:
            return False
        duration = time.time() - start_time
        return duration > threshold

    @staticmethod
    def calculate_delay(scheduled_time: float) -> float:
        """
        计算延迟时间

        Args:
            scheduled_time: 计划时间

        Returns:
            float: 延迟时间（秒），最小为0
        """
        current_time = time.time()
        return max(scheduled_time - current_time, 0)
