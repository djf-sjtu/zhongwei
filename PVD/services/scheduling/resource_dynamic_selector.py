# -*- coding: utf-8 -*-
# services/scheduling/resource_selector.py - 资源选择服务
"""
资源选择服务
职责：为wafer动态选择LL、TBS等资源
"""
import logging
from typing import Optional

from entities import SchedulingWafer
from config import SystemConfig, ConfigService
from utils import is_aligner, LocationParser
from models import module_id_to_str


class ResourceSelector:
    """资源动态选择器"""

    def __init__(self, system):
        self.system = system

    def select_next_location_for_wafer(self, wafer: SchedulingWafer) -> Optional[str]:
        """
        为wafer动态选择下一个位置
        
        只处理需要动态选择的工艺：LL、TBS
        """
        if not wafer.assignment_queue:
            return None

        next_assignment = wafer.assignment_queue[0]

        # 已有具体位置，直接返回
        if next_assignment.to_location:
            return next_assignment.to_location

        # 获取下一个工艺步骤
        next_step = wafer.get_next_step()
        if not next_step or not next_step.target_modules:
            return None

        process_type = ConfigService.get_process_type(
            module_id_to_str(next_step.target_modules[0])
        )

        # 动态选择（只有LL和TBS）
        if process_type == 'LL':
            location = self._select_ll(wafer)
        elif process_type == 'TBS':
            location = self._select_tbs(wafer)
        else:
            return None  # 其他工艺不需要动态选择

        # 更新assignment并修正后续任务
        if location:
            next_assignment.to_location = location
            self._fix_next_task_from_location(wafer, location)
            logging.info(f"动态选择: Wafer {wafer.wafer_id} {process_type} → {location}")

        return location

    def _select_ll(self, wafer: SchedulingWafer) -> Optional[str]:
        """
        选择LL
        
        根据wafer当前位置自动判断：
        - 从ALIGNER出来 → 进片槽
        - 从其他地方出来 → 出片槽
        """
        current_location = wafer.current_location_id

        # 判断槽位类型
        slot_type = 'in' if is_aligner(current_location) else 'out'

        # 查找可用的LL
        for ll_id, config in SystemConfig.LL_MODULES.items():
            if config.get('type') == slot_type:
                for layer in range(1, config['layers'] + 1):
                    location_id = f"{ll_id}_{layer}"
                    location = self.system.get_location_by_id(location_id)
                    
                    if (location and location.busy == 0 and 
                        location.booked_wafer_id is None):
                        return location_id

        return None

    def _select_tbs(self, wafer: SchedulingWafer) -> Optional[str]:
        """选择TBS - 必须空闲且未被预订"""

        # 判断是否需要冷却
        base_module = LocationParser.get_base_module_id(wafer.current_location_id)
        current_zone = ConfigService.get_module_zone(base_module)
        needs_cooling = (current_zone == "TMB")
        
        angle = wafer.base.angle.value
        tbs_candidates = ConfigService.get_tbs_by_angle(angle, needs_cooling)

        # 查找第一个可用的TBS
        for tbs_id in tbs_candidates:
            config = SystemConfig.TBS_MODULES[tbs_id]
            for layer in range(1, config['layers'] + 1):
                location_id = f"{tbs_id}_{layer}"
                location = self.system.get_location_by_id(location_id)
                
                if (location and location.busy == 0 and 
                    location.booked_wafer_id is None):
                    return location_id

        return None

    def _fix_next_task_from_location(self, wafer: SchedulingWafer, current_location: str):
        """修正下一个任务的from_location"""
        if len(wafer.assignment_queue) > 1:
            next_task = wafer.assignment_queue[1]
            if not next_task.from_location:
                next_task.from_location = current_location
                logging.debug(f"修正下一任务起点: {current_location} → {next_task.to_location}")
