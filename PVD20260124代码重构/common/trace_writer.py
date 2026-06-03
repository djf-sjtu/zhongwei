# -*- coding: utf-8 -*-
# trace_writer.py - 结构化事件流（用于重构等价性比对）
"""
TraceWriter — 把仿真中的关键调度决策写到一份 JSONL 文件，
用于"重构前 vs 重构后"的事件流 bit-equal 比对。

设计原则：
1. 被动观察：只 emit，不改变任何调度决策、不改变状态变更顺序
2. 与人类日志解耦：独立文件，不依赖 logging
3. 无 wall-clock 时间戳：避免 wall-clock 漂移导致 diff 假阳性
4. 每条事件带 wafer_seq（按 wafer_id 分桶后的自增序号）：
   多线程下日志行交错没关系，比对时按 wafer_id 分桶 + 桶内排序即可
5. 未 init 时 emit 静默忽略，避免污染生产用法
"""
import json
import threading
from pathlib import Path
from typing import Optional, Any


class _TraceWriter:
    """线程安全的 JSONL trace 写入器。单例。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._file = None
        self._path: Optional[Path] = None
        # wafer_id -> 自增序号
        self._wafer_seq: dict = {}
        # 没有 wafer_id 的事件用全局序号（路径规划前可能尚未关联）
        self._global_seq = 0

    def init(self, path: str) -> None:
        """开启 trace；后续 emit 会写到该文件。重复 init 会先关闭旧文件。"""
        with self._lock:
            if self._file is not None:
                self._file.close()
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._file = p.open('w', encoding='utf-8')
            self._path = p
            self._wafer_seq.clear()
            self._global_seq = 0

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None

    def is_active(self) -> bool:
        return self._file is not None

    def emit(self, event: str, **fields: Any) -> None:
        """写一条事件。未 init 时静默。"""
        if self._file is None:
            return
        with self._lock:
            if self._file is None:
                return
            wafer_id = fields.get('wafer_id')
            if wafer_id is not None:
                seq = self._wafer_seq.get(wafer_id, 0) + 1
                self._wafer_seq[wafer_id] = seq
                fields['wafer_seq'] = seq
            else:
                self._global_seq += 1
                fields['global_seq'] = self._global_seq

            record = {'event': event, **fields}
            # ensure_ascii=False 保留中文，sort_keys 让字段顺序稳定
            self._file.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            self._file.write('\n')
            self._file.flush()


# 模块级单例
_writer = _TraceWriter()


def init_trace(path: str) -> None:
    """开启 trace 输出到 path。"""
    _writer.init(path)


def close_trace() -> None:
    """关闭 trace 文件。"""
    _writer.close()


def emit(event: str, **fields: Any) -> None:
    """写一条 trace 事件。未 init 时是 no-op。"""
    _writer.emit(event, **fields)


def is_active() -> bool:
    return _writer.is_active()
