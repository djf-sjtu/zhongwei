#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diff_trace.py — 比对两份等价性 trace JSONL 文件。

比对规则（"重合前缀 bit-equal"）：
1. 按 wafer_id 分桶（无 wafer_id 的事件归到 __global__ 桶）
2. 桶内按 wafer_seq（或 global_seq）排序
3. 对每个桶取 min(len_a, len_b) 作为重合前缀长度
4. 重合前缀的每条事件必须完全相等（忽略 wafer_seq / global_seq 元数据本身）
5. 报告：每桶事件数对比 + 第一处差异 + 整体结论

退出码：
  0 = 重合前缀 bit-equal（通过）
  1 = 发现差异（失败）
  2 = 输入错误
"""
import argparse
import json
import sys
from pathlib import Path


META_FIELDS = ('wafer_seq', 'global_seq')


def load_trace(path: Path) -> list:
    if not path.exists():
        print(f"❌ 文件不存在: {path}", file=sys.stderr)
        sys.exit(2)
    events = []
    with path.open('r', encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"❌ {path}:{line_no} JSON 解析失败: {e}", file=sys.stderr)
                sys.exit(2)
    return events


def bucket_events(events: list) -> dict:
    """按 wafer_id 分桶；桶内按 seq 排序。"""
    buckets: dict = {}
    for ev in events:
        key = ev.get('wafer_id', '__global__')
        buckets.setdefault(key, []).append(ev)
    for key, evs in buckets.items():
        if key == '__global__':
            evs.sort(key=lambda e: e.get('global_seq', 0))
        else:
            evs.sort(key=lambda e: e.get('wafer_seq', 0))
    return buckets


def strip_meta(ev: dict) -> dict:
    """去掉元序号字段，比对真正的事件内容。"""
    return {k: v for k, v in ev.items() if k not in META_FIELDS}


def diff_buckets(a_buckets: dict, b_buckets: dict) -> tuple:
    """
    返回 (passed, summary_lines, first_diff_lines)
    """
    summary = []
    first_diff = None

    all_keys = sorted(set(a_buckets) | set(b_buckets),
                      key=lambda k: (k == '__global__', k))
    total_a_events = 0
    total_b_events = 0
    total_prefix = 0
    total_diffs_in_prefix = 0

    for key in all_keys:
        a_evs = a_buckets.get(key, [])
        b_evs = b_buckets.get(key, [])
        la, lb = len(a_evs), len(b_evs)
        prefix = min(la, lb)
        total_a_events += la
        total_b_events += lb
        total_prefix += prefix

        bucket_diff = 0
        for i in range(prefix):
            stripped_a = strip_meta(a_evs[i])
            stripped_b = strip_meta(b_evs[i])
            if stripped_a != stripped_b:
                bucket_diff += 1
                if first_diff is None:
                    first_diff = (key, i, stripped_a, stripped_b)
        total_diffs_in_prefix += bucket_diff

        marker = '✓' if bucket_diff == 0 else f'✗({bucket_diff})'
        summary.append(
            f"  wafer={key!s:>8}  a={la:>4}  b={lb:>4}  "
            f"prefix={prefix:>4}  {marker}"
        )

    summary.insert(0, f"按 wafer_id 分桶比对（共 {len(all_keys)} 个桶）:")
    summary.append("")
    summary.append(
        f"事件总数:  a={total_a_events}  b={total_b_events}  "
        f"差异={abs(total_a_events - total_b_events)}  "
        f"({100 * abs(total_a_events - total_b_events) / max(total_a_events, 1):.1f}%)"
    )
    summary.append(f"重合前缀:  {total_prefix} 条事件")
    summary.append(f"前缀中差异数: {total_diffs_in_prefix}")

    diff_lines = []
    if first_diff:
        key, i, sa, sb = first_diff
        diff_lines.append(f"首个差异位于 wafer={key!s} 桶内第 {i} 个事件:")
        diff_lines.append(f"  A: {json.dumps(sa, ensure_ascii=False, sort_keys=True)}")
        diff_lines.append(f"  B: {json.dumps(sb, ensure_ascii=False, sort_keys=True)}")

    return total_diffs_in_prefix == 0, summary, diff_lines


def main():
    parser = argparse.ArgumentParser(
        description='比对两份 trace JSONL 的"重合前缀 bit-equal"'
    )
    parser.add_argument('a', help='第一份 trace（如 baseline）')
    parser.add_argument('b', help='第二份 trace（如重构后）')
    args = parser.parse_args()

    a_path = Path(args.a)
    b_path = Path(args.b)

    a_events = load_trace(a_path)
    b_events = load_trace(b_path)

    print(f"A: {a_path}  ({len(a_events)} 事件)")
    print(f"B: {b_path}  ({len(b_events)} 事件)")
    print()

    a_buckets = bucket_events(a_events)
    b_buckets = bucket_events(b_events)

    passed, summary, diff_lines = diff_buckets(a_buckets, b_buckets)

    for line in summary:
        print(line)
    print()

    if passed:
        print("✅ PASS: 重合前缀 bit-equal")
        sys.exit(0)
    else:
        print("❌ FAIL: 重合前缀存在差异")
        print()
        for line in diff_lines:
            print(line)
        sys.exit(1)


if __name__ == '__main__':
    main()
