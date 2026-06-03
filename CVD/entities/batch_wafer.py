# -*- coding: utf-8 -*-
# entities/batch_wafer.py - 批处理Wafer包装器
"""
CVD平台批处理Wafer
对调度系统来说就是一个普通wafer，占用一个位置ID
"""
import logging


class BatchWafer:
    """
    批处理Wafer - 包装两片wafer

    设计理念:
    - 对调度系统: 表现为一个普通wafer，占用一个位置ID
    - 对传输执行: 内部包含left和right两片wafer
    """

    def __init__(self, wafer_left, wafer_right):
        """
        初始化批处理wafer

        Args:
            wafer_left: 左槽位wafer
            wafer_right: 右槽位wafer

        Raises:
            AssertionError: 如果两片wafer不兼容
        """
        # 验证兼容性
        assert wafer_left.associated_sequence == wafer_right.associated_sequence, \
            "批处理wafer必须有相同的sequence"
        assert wafer_left.angle == wafer_right.angle, \
            "批处理wafer必须有相同的angle"

        # Batch标识 (使用left wafer的ID)
        # 拼接两个id

        self.batch_id = f'{wafer_left.wafer_id}_{wafer_right.wafer_id}'

        # 两片wafer
        self.left_wafer = wafer_left
        self.right_wafer = wafer_right

        # 继承共享属性 (从left_wafer)
        self.source_foup = wafer_left.source_foup
        self.source_foup_slot = wafer_left.source_foup_slot
        self.associated_sequence = wafer_left.associated_sequence
        self.angle = wafer_left.angle

    @property
    def wafer_id(self):
        """
        对外暴露的wafer_id

        这个属性让BatchWafer对调度系统来说就像一个普通wafer
        调度系统通过wafer_id来识别和管理wafer，不需要知道这是batch

        Returns:
            int: batch_id (等于left_wafer的wafer_id)
        """
        return self.batch_id

    def __repr__(self):
        """调试输出"""
        return f"Batch[{self.left_wafer.wafer_id},{self.right_wafer.wafer_id}]"

    def __str__(self):
        """字符串表示"""
        return f"Batch[{self.left_wafer.wafer_id},{self.right_wafer.wafer_id}]"


# ================================================================
# 辅助函数
# ================================================================

def is_batch_wafer(wafer) -> bool:
    """
    判断是否是批处理wafer

    Args:
        wafer: wafer对象

    Returns:
        bool: 是否是BatchWafer
    """
    return isinstance(wafer, BatchWafer)


def create_batches_from_wafers(wafers: list) -> list:
    """
    从wafer列表创建batches

    每两片wafer组成一个batch，如果是奇数个wafer，最后一个保持单片

    Args:
        wafers: wafer列表

    Returns:
        list: batch列表 (包含BatchWafer和可能的单片Wafer)

    Examples:
        >>> wafers = [w1, w2, w3, w4, w5]
        >>> batches = create_batches_from_wafers(wafers)
        >>> len(batches)  # 3个元素
        3
        >>> # batches[0] = Batch(w1, w2)
        >>> # batches[1] = Batch(w3, w4)
        >>> # batches[2] = w5 (单片)
    """
    batches = []

    for i in range(0, len(wafers), 2):
        if i + 1 < len(wafers):
            # 配对成batch
            batch = BatchWafer(wafers[i], wafers[i + 1])
            batches.append(batch)
        else:
            # 奇数个wafer，最后一个单独
            batches.append(wafers[i])
    return batches


def get_batch_wafer_count(batch_or_wafer) -> int:
    """
    获取wafer数量

    Args:
        batch_or_wafer: BatchWafer或普通Wafer

    Returns:
        int: wafer数量 (batch返回2，单片返回1)
    """
    return 2 if is_batch_wafer(batch_or_wafer) else 1


def get_all_wafer_ids(batch_or_wafer) -> list:
    """
    获取所有wafer ID

    Args:
        batch_or_wafer: BatchWafer或普通Wafer

    Returns:
        list: wafer ID列表
    """
    if is_batch_wafer(batch_or_wafer):
        return [batch_or_wafer.left_wafer.wafer_id,
                batch_or_wafer.right_wafer.wafer_id]
    else:
        return [batch_or_wafer.wafer_id]