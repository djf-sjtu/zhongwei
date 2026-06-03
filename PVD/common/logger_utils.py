# -*- coding: utf-8 -*-
# logger_utils.py - 简化的日志格式化工具
"""
统一的日志输出格式 - 简化版
"""
import logging
from config import SystemConfig

class LogIcon:
    """简化的日志图标 - 只区分大类"""
    SYSTEM = "🚀"      # 系统启动/停止/Job开始结束
    PLAN = "📋"        # 路径规划、调度决策
    TRANSPORT = "🚚"   # 所有传输相关
    PROCESS = "⚙️"     # 所有工艺处理（Chamber/Aligner/冷却）
    COMPLETE = "✅"    # 完成事件
    WARNING = "⚠️"     # 警告信息
    ERROR = "❌"       # 错误信息
    WAIT = "⏳"        # 等待相关
    MACRO = "🔧"    # 宏任务相关


class ChamberNameFormatter(logging.Formatter):
    """自动替换Chamber名称的日志格式化器"""

    def __init__(self, fmt=None, datefmt=None):
        super().__init__(fmt, datefmt)
        # 构建替换模式：匹配 CHAMBER_ID 或 CHAMBER_ID_1 格式
        self.replacements = {}
        for internal_name, display_name in SystemConfig.CHAMBER_DISPLAY_NAMES.items():
            # 匹配带层号的格式，如 TREAT1_A_1
            self.replacements[f"{internal_name}_1"] = display_name
            # 匹配不带层号的格式，如 TREAT1_A
            self.replacements[internal_name] = display_name

    def format(self, record):
        # 先用父类格式化
        message = super().format(record)

        # 替换所有Chamber名称（先替换长的，避免部分匹配）
        for internal_name, display_name in sorted(self.replacements.items(),
                                                  key=lambda x: len(x[0]),
                                                  reverse=True):
            message = message.replace(internal_name, display_name)

        return message


def setup_logger():
    """配置日志系统"""
    # 创建自定义格式化器
    formatter = ChamberNameFormatter(
        fmt='%(asctime)s.%(msecs)03d | %(message)s',
        datefmt='%H:%M:%S'
    )

    # 配置根日志记录器
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logging.root.handlers = []
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)