# -*- coding: utf-8 -*-
# domain/resource_validator.py - 资源验证服务
"""
资源验证服务
职责：验证资源是否就绪（从utils迁移的业务逻辑）
"""
from utils import is_foup, is_ll, is_tbs, is_aligner, is_chamber


class ResourceValidator:
    """资源验证服务"""

    @staticmethod
    def is_next_step_ready(wafer, next_assignment, system) -> bool:
        """
        判断wafer的下一步是否ready
        
        这是核心的调度逻辑，从utils.py迁移过来
        
        Args:
            wafer: SchedulingWafer
            next_assignment: TransportTask
            system: SchedulingSystem
            
        Returns:
            bool: 是否ready
        """
        target_location_id = next_assignment.to_location
        target_location = system.get_location_by_id(target_location_id)

        if not target_location:
            return False

        # FOUP：总是ready
        if is_foup(target_location_id):
            return True

        # LL：检查是否空闲且未被预订（或被当前wafer预订）
        if is_ll(target_location_id):
            return (target_location.busy == 0 and
                    target_location.is_pumping == False and
                    (target_location.booked_wafer_id is None or
                     target_location.booked_wafer_id == wafer.wafer_id))

        # TBS：检查是否空闲且未被预订（或被当前wafer预订）
        if is_tbs(target_location_id):
            return (target_location.busy == 0 and
                    (target_location.booked_wafer_id is None or
                     target_location.booked_wafer_id == wafer.wafer_id))

        # Aligner：检查是否空闲且未被预订（或被当前wafer预订）
        if is_aligner(target_location_id):
            return (target_location.busy == 0 and
                    (target_location.booked_wafer_id is None or
                     target_location.booked_wafer_id == wafer.wafer_id))

        # Chamber：必须空闲，且队首任务是该wafer
        if is_chamber(target_location_id):
            if target_location.busy != 0:
                return False

            if not target_location.task_queue:
                return False

            first_task = target_location.task_queue[0]
            return first_task.wafer_id == wafer.wafer_id

        return False

    @staticmethod
    def is_location_available(location, wafer_id: int = None) -> bool:
        """
        检查位置是否可用
        
        Args:
            location: 位置实体
            wafer_id: wafer ID（用于检查预订）
            
        Returns:
            bool: 是否可用
        """
        if location.busy != 0:
            return False

        # 检查预订状态
        if hasattr(location, 'booked_wafer_id'):
            if location.booked_wafer_id is not None:
                # 如果已被预订，只有相同wafer才能使用
                return location.booked_wafer_id == wafer_id

        return True
