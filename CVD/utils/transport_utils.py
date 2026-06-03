# utils/transport_utils.py

from config import SystemConfig
from utils import LocationParser


def  calc_transport_time(from_loc: str, to_loc: str) -> float:
    """计算传输时间"""
    #注意，这个函数是估计传输时间，估计不准，关键在于Z轴移动次数，与robot在执行本次调度时的Z轴高度有关，
    # （而且实际上robot从起始位置到达from_loc也可需要移动时间）
    base_time = SystemConfig.TRANSPORT_BASE_TIME + SystemConfig.PICK_PLACE_TIME * 2

    if from_loc:
        from_z = LocationParser.get_z_level_for_location(from_loc) if from_loc else 'LL_TBS'
        to_z = LocationParser.get_z_level_for_location(to_loc) if to_loc else 'LL_TBS'

        if from_z != to_z:
            base_time += SystemConfig.Z_MOVE_TIME * 2

    return base_time