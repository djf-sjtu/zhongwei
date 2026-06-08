#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare legacy and continuity-first CVD fault rescheduling."""
import argparse
import copy
import json
import logging
import sys
import threading
import time
from pathlib import Path


CVD_ROOT = Path(__file__).resolve().parents[1]
if str(CVD_ROOT) not in sys.path:
    sys.path.insert(0, str(CVD_ROOT))

from config import SystemConfig  # noqa: E402
from main_scheduler import SchedulerSimulation  # noqa: E402


logging.disable(logging.CRITICAL)


BASE_SCHEDULING_CONFIG = copy.deepcopy(SystemConfig.SCHEDULING_CONFIG)


def reset_config():
    SystemConfig.SCHEDULING_CONFIG = copy.deepcopy(BASE_SCHEDULING_CONFIG)


def _round_time(value):
    if value is None:
        return None
    return round(float(value), 3)


def _serialize_step(step, now):
    return {
        'process_type': step.process_type,
        'location_id': step.location_id,
        'estimated_arrival': _round_time(step.estimated_arrival),
        'estimated_departure': _round_time(step.estimated_departure),
        'arrival_after_s': _round_time(step.estimated_arrival - now),
        'departure_after_s': _round_time(step.estimated_departure - now),
    }


def _serialize_wafer_plan(wafer, now):
    return {
        'wafer_id': str(wafer.wafer_id),
        'current_location_id': wafer.current_location_id,
        'current_step_index': wafer.current_step_index,
        'scheduled_leave_time': _round_time(wafer.scheduled_leave_time),
        'scheduled_leave_after_s': (
            _round_time(wafer.scheduled_leave_time - now)
            if wafer.scheduled_leave_time is not None else None
        ),
        'transport_not_before': {
            str(step_idx): _round_time(gate - now)
            for step_idx, gate in sorted(wafer.transport_not_before.items())
        },
        'steps': [
            _serialize_step(step, now)
            for step in wafer.path_plan
        ],
    }


def _collect_plan_snapshot(sim, label: str):
    now = time.time()
    wafers = []
    for wafer in sorted(sim.coordinator.system.wafers.values(), key=lambda item: str(item.wafer_id)):
        if not wafer.path_plan:
            continue
        wafers.append(_serialize_wafer_plan(wafer, now))
    return {
        'label': label,
        'captured_at': _round_time(now),
        'plans': wafers,
    }


def _step_signature(step):
    return (
        step['process_type'],
        step['location_id'],
        step['arrival_after_s'],
        step['departure_after_s'],
    )


def _diff_plan_snapshots(before: dict, after: dict):
    before_map = {plan['wafer_id']: plan for plan in before.get('plans', [])}
    after_map = {plan['wafer_id']: plan for plan in after.get('plans', [])}
    changed = []
    for wafer_id in sorted(set(before_map) | set(after_map), key=str):
        old_plan = before_map.get(wafer_id)
        new_plan = after_map.get(wafer_id)
        if old_plan is None or new_plan is None:
            changed.append({
                'wafer_id': wafer_id,
                'change_type': 'added' if old_plan is None else 'removed',
                'before': old_plan,
                'after': new_plan,
            })
            continue

        old_steps = [_step_signature(step) for step in old_plan['steps']]
        new_steps = [_step_signature(step) for step in new_plan['steps']]
        if (
            old_steps != new_steps
            or old_plan['scheduled_leave_after_s'] != new_plan['scheduled_leave_after_s']
            or old_plan['transport_not_before'] != new_plan['transport_not_before']
        ):
            changed.append({
                'wafer_id': wafer_id,
                'change_type': 'updated',
                'before': {
                    'current_location_id': old_plan['current_location_id'],
                    'current_step_index': old_plan['current_step_index'],
                    'scheduled_leave_after_s': old_plan['scheduled_leave_after_s'],
                    'transport_not_before': old_plan['transport_not_before'],
                    'steps': old_plan['steps'],
                },
                'after': {
                    'current_location_id': new_plan['current_location_id'],
                    'current_step_index': new_plan['current_step_index'],
                    'scheduled_leave_after_s': new_plan['scheduled_leave_after_s'],
                    'transport_not_before': new_plan['transport_not_before'],
                    'steps': new_plan['steps'],
                },
            })
    return changed


def run_strategy(
        strategy: str,
        wafer_count: int,
        fault_at: float,
        unfault_at: float,
        chamber_id: str = 'BULK_A',
        max_runtime: float = None) -> dict:
    reset_config()
    SystemConfig.SCHEDULING_CONFIG['fault_replan_strategy'] = strategy

    sim = SchedulerSimulation()
    job = sim.create_job(job_id=1, sequence_type='basic', wafer_count=wafer_count)
    plan_snapshots = []

    def on_fault():
        plan_snapshots.append(_collect_plan_snapshot(sim, 'before_fault'))
        sim.coordinator.fault_chamber(chamber_id)
        plan_snapshots.append(_collect_plan_snapshot(sim, 'after_fault'))

    def on_unfault():
        plan_snapshots.append(_collect_plan_snapshot(sim, 'before_unfault'))
        sim.coordinator.unfault_chamber(chamber_id)
        plan_snapshots.append(_collect_plan_snapshot(sim, 'after_unfault'))

    timers = [
        threading.Timer(fault_at, on_fault),
        threading.Timer(unfault_at, on_unfault),
    ]
    for timer in timers:
        timer.daemon = True
        timer.start()

    run_error = []

    def run_simulation():
        try:
            sim.run([job])
        except Exception as exc:  # pragma: no cover - diagnostic wrapper
            run_error.append(exc)

    runner = threading.Thread(target=run_simulation, daemon=True)
    runner.start()
    runner.join(timeout=max_runtime)
    timed_out = runner.is_alive()
    if timed_out:
        sim.coordinator.stop_system()
        runner.join(timeout=5.0)

    for timer in timers:
        timer.cancel()

    if run_error:
        raise run_error[0]

    summary = sim.coordinator.metrics.summary()
    summary['timed_out'] = timed_out
    summary['status'] = sim.coordinator.get_system_status()
    summary['plan_snapshots'] = plan_snapshots
    snapshot_map = {item['label']: item for item in plan_snapshots}
    summary['plan_deltas'] = {
        'fault': _diff_plan_snapshots(
            snapshot_map.get('before_fault', {}),
            snapshot_map.get('after_fault', {}),
        ),
        'unfault': _diff_plan_snapshots(
            snapshot_map.get('before_unfault', {}),
            snapshot_map.get('after_unfault', {}),
        ),
    }
    return summary


def compare_metrics(baseline: dict, optimized: dict, makespan_tolerance: float = 1.0) -> dict:
    return {
        'baseline': baseline,
        'optimized': optimized,
        'affected_wafer_delta': (
            optimized['affected_wafer_count'] - baseline['affected_wafer_count']
        ),
        'makespan_delta': optimized['makespan'] - baseline['makespan'],
        'reroute_count_delta': optimized['reroute_count'] - baseline['reroute_count'],
        'passes_quality_non_regression': (
            optimized['affected_wafer_count'] <= baseline['affected_wafer_count']
        ),
        'passes_makespan_tiebreak': (
            optimized['affected_wafer_count'] != baseline['affected_wafer_count']
            or optimized['makespan'] <= baseline['makespan'] + makespan_tolerance
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wafer-count', type=int, default=5)
    parser.add_argument('--fault-at', type=float, default=60.0)
    parser.add_argument('--unfault-at', type=float, default=180.0)
    parser.add_argument('--chamber-id', type=str, default='BULK_A')
    parser.add_argument(
        '--max-runtime',
        type=float,
        default=None,
        help='Stop each strategy after this many wall-clock seconds and emit partial metrics.',
    )
    parser.add_argument('--output', type=str, default='CVD/logs/fault_rescheduling_eval.json')
    args = parser.parse_args()

    baseline = run_strategy(
        'legacy_direct',
        args.wafer_count,
        args.fault_at,
        args.unfault_at,
        chamber_id=args.chamber_id,
        max_runtime=args.max_runtime,
    )
    optimized = run_strategy(
        'continuity_pool',
        args.wafer_count,
        args.fault_at,
        args.unfault_at,
        chamber_id=args.chamber_id,
        max_runtime=args.max_runtime,
    )
    comparison = compare_metrics(baseline, optimized)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(comparison, ensure_ascii=False, sort_keys=True, indent=2),
        encoding='utf-8',
    )
    print(json.dumps(comparison, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
