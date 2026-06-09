#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare legacy and continuity-first PVD fault rescheduling."""
import argparse
import copy
import json
import logging
import sys
import threading
from pathlib import Path


PVD_ROOT = Path(__file__).resolve().parents[1]
if str(PVD_ROOT) not in sys.path:
    sys.path.insert(0, str(PVD_ROOT))

from config import SystemConfig  # noqa: E402
from main_scheduler import SchedulerSimulation  # noqa: E402


logging.disable(logging.CRITICAL)


BASE_PROCESS_TIMES = copy.deepcopy(SystemConfig.PROCESS_TIMES)
BASE_ALIGNER_CONFIG = copy.deepcopy(SystemConfig.ALIGNER_CONFIG)
BASE_DRY_PUMP_CONFIG = copy.deepcopy(SystemConfig.DRY_PUMP_CONFIG)
BASE_SCHEDULING_CONFIG = copy.deepcopy(SystemConfig.SCHEDULING_CONFIG)
BASE_COOLING_TIME_SECONDS = SystemConfig.COOLING_TIME_SECONDS
BASE_Z_MOVE_TIME = SystemConfig.Z_MOVE_TIME
BASE_PICK_PLACE_TIME = SystemConfig.PICK_PLACE_TIME
BASE_TRANSPORT_BASE_TIME = SystemConfig.TRANSPORT_BASE_TIME
LOADED_TIME_SCALE = SystemConfig.TIME_SCALE


def reset_config():
    SystemConfig.PROCESS_TIMES = copy.deepcopy(BASE_PROCESS_TIMES)
    SystemConfig.ALIGNER_CONFIG = copy.deepcopy(BASE_ALIGNER_CONFIG)
    SystemConfig.DRY_PUMP_CONFIG = copy.deepcopy(BASE_DRY_PUMP_CONFIG)
    SystemConfig.SCHEDULING_CONFIG = copy.deepcopy(BASE_SCHEDULING_CONFIG)
    SystemConfig.COOLING_TIME_SECONDS = BASE_COOLING_TIME_SECONDS
    SystemConfig.Z_MOVE_TIME = BASE_Z_MOVE_TIME
    SystemConfig.PICK_PLACE_TIME = BASE_PICK_PLACE_TIME
    SystemConfig.TRANSPORT_BASE_TIME = BASE_TRANSPORT_BASE_TIME
    SystemConfig.TIME_SCALE = LOADED_TIME_SCALE


def apply_time_scale(scale: float, storage_threshold: float = None):
    """Apply an absolute scale to values already loaded at LOADED_TIME_SCALE.

    storage_threshold is expressed in unscaled business seconds, consistent with
    SystemConfig.SCHEDULING_CONFIG.
    """
    factor = scale / LOADED_TIME_SCALE
    for key in list(SystemConfig.PROCESS_TIMES):
        SystemConfig.PROCESS_TIMES[key] *= factor
    SystemConfig.ALIGNER_CONFIG['process_time_seconds'] *= factor
    SystemConfig.DRY_PUMP_CONFIG['pump_time_seconds'] *= factor
    SystemConfig.DRY_PUMP_CONFIG['vent_time_seconds'] *= factor
    SystemConfig.COOLING_TIME_SECONDS *= factor
    SystemConfig.Z_MOVE_TIME *= factor
    SystemConfig.PICK_PLACE_TIME *= factor
    SystemConfig.TRANSPORT_BASE_TIME *= factor
    SystemConfig.SCHEDULING_CONFIG['periodic_check_interval'] *= factor
    SystemConfig.SCHEDULING_CONFIG['scheduling_cycle_interval_seconds'] *= factor
    SystemConfig.SCHEDULING_CONFIG['cleaning_check_interval_seconds'] *= factor
    if storage_threshold is None:
        SystemConfig.SCHEDULING_CONFIG['chamber_storage_warning_threshold'] *= factor
    else:
        SystemConfig.SCHEDULING_CONFIG['chamber_storage_warning_threshold'] = storage_threshold * scale
    SystemConfig.TIME_SCALE = scale


def run_strategy(
        strategy: str,
        wafer_count: int,
        time_scale: float,
        fault_at: float,
        unfault_at: float,
        chamber_id: str = 'CVD2_A_1',
        storage_threshold: float = None,
        max_runtime: float = None) -> dict:
    reset_config()
    apply_time_scale(time_scale, storage_threshold=storage_threshold)
    SystemConfig.SCHEDULING_CONFIG['fault_replan_strategy'] = strategy

    sim = SchedulerSimulation()
    job = sim.create_job(job_id=1, sequence_type='complex', wafer_count=wafer_count)
    timers = [
        threading.Timer(fault_at, sim.coordinator.fault_chamber, args=(chamber_id,)),
        threading.Timer(unfault_at, sim.coordinator.unfault_chamber, args=(chamber_id,)),
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
    return summary


def compare_metrics(baseline: dict, optimized: dict, makespan_tolerance: float = 0.25) -> dict:
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
    parser.add_argument('--wafer-count', type=int, default=3)
    parser.add_argument('--time-scale', type=float, default=0.1)
    parser.add_argument('--fault-at', type=float, default=180.0)
    parser.add_argument('--unfault-at', type=float, default=300.0)
    parser.add_argument('--chamber-id', type=str, default='CVD2_A_1')
    parser.add_argument(
        '--storage-threshold',
        type=float,
        default=None,
        help='Unscaled business-time threshold in seconds; default is 120.',
    )
    parser.add_argument(
        '--max-runtime',
        type=float,
        default=None,
        help='Stop each strategy after this many wall-clock seconds and emit partial metrics.',
    )
    parser.add_argument('--output', type=str, default='PVD/logs/fault_rescheduling_eval.json')
    args = parser.parse_args()

    baseline = run_strategy(
        'legacy_direct',
        args.wafer_count,
        args.time_scale,
        args.fault_at,
        args.unfault_at,
        chamber_id=args.chamber_id,
        storage_threshold=args.storage_threshold,
        max_runtime=args.max_runtime,
    )
    optimized = run_strategy(
        'continuity_pool',
        args.wafer_count,
        args.time_scale,
        args.fault_at,
        args.unfault_at,
        chamber_id=args.chamber_id,
        storage_threshold=args.storage_threshold,
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
