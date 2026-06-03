# -*- coding: utf-8 -*-
# entities/scheduling_entities.py - 调度实体
"""
调度实体 - 只包含状态和简单查询方法
职责：状态管理，不包含复杂业务逻辑
"""
import time
import threading
from typing import Optional, List
from dataclasses import dataclass

from models import Wafer, Chamber, module_id_to_str


# ================================================================
# 任务数据结构
# ================================================================

@dataclass
class TransportTask:
    """传输任务"""
    wafer_id: int
    from_location: str
    to_location: str


@dataclass
class ChamberTask:
    """Chamber任务"""
    task_id: str
    task_type: str  # 'macro' 或 'wafer_process'
    macro_task: Optional[object] = None
    wafer_id: Optional[int] = None
    duration: float = 0.0


@dataclass
class PathStep:
    """路径步骤 - 只记录计划时间"""
    process_type: str           # 工艺类型
    location_id: str           # 位置ID（可能为空，表示动态选择）
    estimated_arrival: float   # 预计到达时间（绝对时间）
    estimated_departure: float # 预计离开时间（绝对时间）


# ================================================================
# 基础实体类
# ================================================================

class SchedulingEntity:
    """调度实体基类"""

    def __init__(self, location_id: str):
        self.location_id = location_id
        self.busy = 0
        self.current_wafer_id: Optional[int] = None


# ================================================================
# Wafer实体
# ================================================================

class SchedulingWafer(SchedulingEntity):
    """Wafer调度实体"""

    def __init__(self, base_wafer: Wafer):
        super().__init__(f"wafer_{base_wafer.wafer_id}")

        # 基础信息
        self.base = base_wafer
        self.wafer_id = base_wafer.wafer_id
        self.current_location_id = ""  # 初始化后设置
        self.current_step_index = 0

        # 时间记录
        self.job_arrival_time = time.time()
        self.storage_start_time: Optional[float] = None

        # 路径规划
        self.assignment_queue: List[TransportTask] = []
        self.scheduled_leave_time: Optional[float] = None
        self.path_plan: List[PathStep] = []  # 保存完整的路径规划

    def get_current_step(self):
        """获取当前工艺步骤"""
        steps = self.base.associated_sequence.sequence_steps
        if self.current_step_index < len(steps):
            return steps[self.current_step_index]
        return None

    def get_next_step(self):
        """获取下一个工艺步骤"""
        steps = self.base.associated_sequence.sequence_steps
        next_index = self.current_step_index + 1
        if next_index < len(steps):
            return steps[next_index]
        return None

    def is_sequence_completed(self) -> bool:
        """检查是否完成所有工艺"""
        total_steps = len(self.base.associated_sequence.sequence_steps)
        return self.current_step_index + 1 >= total_steps

    def move_to_next_location(self, new_location_id: str):
        """移动到下一个位置"""
        self.current_location_id = new_location_id
        self.current_step_index += 1


# ================================================================
# Chamber实体
# ================================================================

class SchedulingChamber(SchedulingEntity):
    """Chamber调度实体 (CVD: 不使用槽位后缀)"""

    def __init__(self, base_chamber: Chamber):

        module_str = module_id_to_str(base_chamber.module_id)
        # CVD: 直接使用模块名作为location_id，不添加_1后缀
        super().__init__(module_str)

        # 基础信息
        self.base = base_chamber
        self.module_id = base_chamber.module_id

        # 任务队列
        self.task_queue: List[ChamberTask] = []
        self.current_task: Optional[ChamberTask] = None

        # 时间记录
        self.task_start_time: Optional[float] = None
        self.last_task_end_time: Optional[float] = None
        self.idle_start_time: Optional[float] = None
        self.last_cleaning_time: Optional[float] = None

        # 处理历史
        self.processing_history: List[tuple] = []

        # 故障状态
        self.is_faulted: bool = False
        self.frozen_task: Optional[ChamberTask] = None
        self._fault_lock = threading.Lock()

    def needs_dry_cleaning(self) -> bool:
        """检查是否需要干洗"""
        return (self.base.film_thickness_counter >=
                self.base.film_thickness_threshold_to_trigger_dry_clean)

    def needs_immediate_preheat(self) -> bool:
        """检查是否需要立即预热"""
        if not self.last_task_end_time:
            return False
        idle_duration = time.time() - self.last_task_end_time
        return idle_duration > self.base.idle_macro_trigger_duration


# ================================================================
# Robot实体
# ================================================================

class SchedulingRobot(SchedulingEntity):
    """机械臂调度实体"""

    def __init__(self, robot_id: str, robot_type: str):
        super().__init__(robot_id)

        # 基础信息
        self.robot_id = robot_id
        self.robot_type = robot_type

        # Z轴状态
        self.current_z_level = "LL_TBS"
        self.is_z_moving = False

        # 传输队列
        self.transport_queue: List[TransportTask] = []

    def is_robot_available(self) -> bool:
        """机械臂是否可用"""
        return self.busy == 0 and not self.is_z_moving


# ================================================================
# LL实体
# ================================================================

class SchedulingLL(SchedulingEntity):
    """LoadLock调度实体"""

    def __init__(self, location_id: str):
        super().__init__(location_id)
        self.booked_wafer_id: Optional[int] = None  # 预订wafer
        self.is_pumping = False


# ================================================================
# TBS实体
# ================================================================

class SchedulingTBS(SchedulingEntity):
    """TBS调度实体"""

    def __init__(self, location_id: str, has_cooling: bool):
        super().__init__(location_id)
        self.has_cooling = has_cooling
        self.booked_wafer_id: Optional[int] = None  # 预订wafer


# ================================================================
# Aligner实体
# ================================================================

class SchedulingAligner(SchedulingEntity):
    """Aligner调度实体 (CVD: 不使用槽位后缀)"""

    def __init__(self, base_aligner):

        module_str = module_id_to_str(base_aligner.module_id)
        # CVD: 直接使用模块名作为location_id，不添加_1后缀
        super().__init__(module_str)

        # 基础信息
        self.base = base_aligner
        self.module_id = base_aligner.module_id

        # 当前处理
        self.current_wafer_start_time: Optional[float] = None
        self.booked_wafer_id: Optional[int] = None


# ================================================================
# FOUP实体
# ================================================================

class SchedulingFOUP(SchedulingEntity):
    """FOUP调度实体"""

    def __init__(self, base_foup):
        super().__init__(module_id_to_str(base_foup.module_id))

        # 基础信息
        self.base = base_foup
        self.module_id = base_foup.module_id