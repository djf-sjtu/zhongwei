# -*- coding: utf-8 -*-
# entities/scheduling_system.py - 调度系统容器
"""
调度系统容器
职责：管理所有调度实体，提供封装的访问接口
"""
from typing import Dict, List, Optional

from models import Wafer, Chamber, FOUP, Aligner, ModuleID
from config import SystemConfig, ConfigService
from .scheduling_entities import (
    SchedulingWafer,
    SchedulingChamber,
    SchedulingLL,
    SchedulingTBS,
    SchedulingAligner,
    SchedulingFOUP,
    SchedulingRobot,
    SchedulingEntity
)
from domain import WaferService
from utils import LocationParser


class SchedulingSystem:
    """调度系统 - 实体容器"""

    def __init__(self):
        # 实体容器
        self.wafers: Dict[int, SchedulingWafer] = {}
        self.chambers: Dict[str, SchedulingChamber] = {}
        self.lls: Dict[str, SchedulingLL] = {}
        self.tbs_locations: Dict[str, SchedulingTBS] = {}
        self.aligners: Dict[str, SchedulingAligner] = {}
        self.foups: Dict[str, SchedulingFOUP] = {}
        self.robots: Dict[str, SchedulingRobot] = {}

        # Dry pump状态
        self.dry_pump_busy = 0

        # 等待队列
        self.waiting_wafers: List[SchedulingWafer] = []

        # 初始化
        self._init_robots()
        self._init_static_locations()

    # ================================================================
    # 初始化方法
    # ================================================================

    def _init_robots(self):
        """初始化机械臂"""
        for robot_id, robot_config in SystemConfig.ROBOT_MODULES.items():
            robot = SchedulingRobot(robot_id, robot_config['type'])
            self.robots[robot_id] = robot

    def _init_static_locations(self):
        """初始化所有静态位置"""
        self._init_chambers()
        self._init_lls()
        self._init_tbs()
        self._init_aligners()
        self._init_foups()

    def _init_chambers(self):
        """初始化Chambers (CVD: 不使用槽位后缀)"""
        for chamber_id in SystemConfig.CHAMBER_MODULES.keys():
            chamber = Chamber(
                module_id=ModuleID(chamber_id),
                online=1,
                busy=0,
                film_thickness_counter=75,
                film_thickness_threshold_to_trigger_dry_clean=100,
                idle_macro_trigger_duration=SystemConfig.CHAMBER_DEFAULT_CONFIG[
                    'idle_macro_trigger_duration_seconds']
            )
            # CVD: 直接使用模块名作为位置ID，不添加_1后缀
            location_id = chamber_id
            self.chambers[location_id] = SchedulingChamber(chamber)

    def _init_lls(self):
        """初始化LoadLocks (CVD: 不遍历layers，直接使用模块名)"""
        for ll_id, config in SystemConfig.LL_MODULES.items():
            # CVD: 不遍历layers，直接用模块名作为位置ID
            # 物理上有左右两个槽位，但调度系统只管理一个位置
            location_id = ll_id
            self.lls[location_id] = SchedulingLL(location_id)

    def _init_tbs(self):
        """初始化TBS (CVD: 不遍历layers，直接使用模块名)"""
        for tbs_id, config in SystemConfig.TBS_MODULES.items():
            has_cooling = config.get('cooling', False)
            # CVD: 不遍历layers，直接用模块名作为位置ID
            # 物理上有左右两个槽位，但调度系统只管理一个位置
            location_id = tbs_id
            self.tbs_locations[location_id] = SchedulingTBS(location_id, has_cooling)

    def _init_aligners(self):
        """初始化Aligners (CVD: 不使用槽位后缀)"""
        for aligner_id in SystemConfig.ALIGNER_MODULES.keys():
            aligner = Aligner(module_id=ModuleID(aligner_id))
            # CVD: 直接使用模块名作为位置ID，不添加_1后缀
            location_id = aligner_id
            self.aligners[location_id] = SchedulingAligner(aligner)

    def _init_foups(self):
        """初始化FOUPs"""
        for foup_id, config in SystemConfig.FOUP_MODULES.items():
            for slot in range(1, config['slots'] + 1):
                slot_location_id = f"{foup_id}_{slot}"
                foup = FOUP(module_id=ModuleID(foup_id))
                self.foups[slot_location_id] = SchedulingFOUP(foup)

    # ================================================================
    # Wafer管理（封装接口）
    # ================================================================

    def add_wafer(self, base_wafer: Wafer) -> SchedulingWafer:
        """
        添加wafer到系统

        Args:
            base_wafer: 基础wafer对象

        Returns:
            SchedulingWafer: 调度wafer实体
        """

        wafer = SchedulingWafer(base_wafer)
        wafer.current_location_id = WaferService.get_foup_location_id(wafer)
        self.wafers[base_wafer.wafer_id] = wafer
        return wafer

    def get_wafer(self, wafer_id: int) -> Optional[SchedulingWafer]:
        """获取wafer"""
        return self.wafers.get(wafer_id)

    def get_schedulable_wafers(self) -> List[SchedulingWafer]:
        """获取可调度的wafers"""
        return [w for w in self.wafers.values()
                if w.busy == 0 and not w.is_sequence_completed()]

    # ================================================================
    # 位置查询（封装接口）
    # ================================================================

    def get_location_by_id(self, location_id: str) -> Optional[SchedulingEntity]:
        """
        根据ID获取位置实体

        Args:
            location_id: 位置ID

        Returns:
            SchedulingEntity: 位置实体，如果不存在返回None
        """
        # 查找顺序：Chamber → Aligner → LL → TBS → FOUP
        if location_id in self.chambers:
            return self.chambers[location_id]
        elif location_id in self.aligners:
            return self.aligners[location_id]
        elif location_id in self.lls:
            return self.lls[location_id]
        elif location_id in self.tbs_locations:
            return self.tbs_locations[location_id]
        elif location_id in self.foups:
            return self.foups[location_id]
        return None

    def get_chamber(self, location_id: str) -> Optional[SchedulingChamber]:
        """获取Chamber"""
        return self.chambers.get(location_id)

    def get_ll(self, location_id: str) -> Optional[SchedulingLL]:
        """获取LL"""
        return self.lls.get(location_id)

    def get_tbs(self, location_id: str) -> Optional[SchedulingTBS]:
        """获取TBS"""
        return self.tbs_locations.get(location_id)

    def get_aligner(self, location_id: str) -> Optional[SchedulingAligner]:
        """获取Aligner"""
        return self.aligners.get(location_id)

    def get_foup(self, location_id: str) -> Optional[SchedulingFOUP]:
        """获取FOUP"""
        return self.foups.get(location_id)

    def get_robot(self, robot_id: str) -> Optional[SchedulingRobot]:
        """获取Robot"""
        return self.robots.get(robot_id)

    # ================================================================
    # 批量查询（封装接口）
    # ================================================================

    def get_available_lls(self, ll_type: str = None) -> List[SchedulingLL]:
        """
        获取可用的LL

        Args:
            ll_type: LL类型 ('in', 'out', None表示所有)

        Returns:
            List[SchedulingLL]: 可用的LL列表
        """
        available = []
        for ll_id, ll in self.lls.items():
            if ll.busy == 0 and not ll.is_pumping:
                if ll_type is None:
                    available.append(ll)
                else:

                    base_module = LocationParser.get_base_module_id(ll_id)
                    if ll_type == 'in' and ConfigService.is_ll_in_slot(base_module):
                        available.append(ll)
                    elif ll_type == 'out' and ConfigService.is_ll_out_slot(base_module):
                        available.append(ll)
        return available

    def get_available_tbs(self, needs_cooling: bool = None) -> List[SchedulingTBS]:
        """
        获取可用的TBS

        Args:
            needs_cooling: 是否需要冷却 (None表示不限)

        Returns:
            List[SchedulingTBS]: 可用的TBS列表
        """
        available = []
        for tbs_id, tbs in self.tbs_locations.items():
            if tbs.busy == 0:
                if needs_cooling is None or tbs.has_cooling == needs_cooling:
                    available.append(tbs)
        return available

    def get_chambers_by_process_type(self, process_type: str) -> List[SchedulingChamber]:
        """
        获取指定工艺类型的所有Chambers

        Args:
            process_type: 工艺类型

        Returns:
            List[SchedulingChamber]: Chamber列表
        """
        result = []
        for chamber_id, chamber in self.chambers.items():

            if LocationParser.get_module_type(chamber_id) == process_type:
                result.append(chamber)
        return result

    # ================================================================
    # 等待队列管理
    # ================================================================

    def add_to_waiting(self, wafer: SchedulingWafer):
        """添加到等待队列"""
        if wafer not in self.waiting_wafers:
            self.waiting_wafers.append(wafer)

    def remove_from_waiting(self, wafer: SchedulingWafer):
        """从等待队列移除"""
        if wafer in self.waiting_wafers:
            self.waiting_wafers.remove(wafer)

    # ================================================================
    # 统计信息
    # ================================================================

    def get_total_wafer_count(self) -> int:
        """获取系统中wafer总数"""
        return len(self.wafers)

    def get_completed_wafer_count(self) -> int:
        """获取已完成wafer数量"""
        return sum(1 for w in self.wafers.values() if w.is_sequence_completed())

    def get_waiting_wafer_count(self) -> int:
        """获取等待中wafer数量"""
        return len(self.waiting_wafers)