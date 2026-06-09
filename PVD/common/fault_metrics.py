# -*- coding: utf-8 -*-
"""Fault-scenario metrics for PVD scheduling."""
import copy
import logging
import json
import time
from pathlib import Path
from typing import Dict, Optional

from config import SystemConfig
from utils import is_chamber


class FaultMetrics:
    """Collects quality and throughput signals without changing scheduling."""

    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.affected_wafer_ids = set()
        self.storage_overruns: Dict[int, float] = {}
        self.temp_park_count = 0
        self.temp_park_events = []
        self.storage_overrun_events = []
        self.reroute_count = 0
        self.completed_wafer_count = 0
        self.fault_replan_candidates: Dict[str, list] = {}
        self.fault_plan_decisions = []
        self.timing_replans = []
        self.ignored_wafer_ids = set()
        self.snapshots = []

    @property
    def threshold(self) -> float:
        return SystemConfig.SCHEDULING_CONFIG.get('chamber_storage_warning_threshold', 120.0)

    def start(self):
        self.start_time = time.time()
        self.end_time = None

    def finish(self):
        self.end_time = time.time()

    def on_process_completed(self, wafer_id: int):
        if wafer_id in self.ignored_wafer_ids:
            return
        self.storage_overruns.setdefault(wafer_id, 0.0)

    def on_transport_started(self, wafer, from_location: str):
        if wafer.wafer_id in self.ignored_wafer_ids:
            return
        if not is_chamber(from_location):
            return
        if wafer.storage_start_time is None:
            return
        storage_duration = time.time() - wafer.storage_start_time
        self._record_storage_duration(wafer.wafer_id, storage_duration)
        if storage_duration > self.threshold:
            self.storage_overrun_events.append({
                'wafer_id': wafer.wafer_id,
                'from_location': from_location,
                'to_location': (
                    wafer.assignment_queue[0].to_location
                    if wafer.assignment_queue else ''
                ),
                'storage_duration': storage_duration,
            })

    def on_temp_parked(self, wafer):
        if wafer.wafer_id in self.ignored_wafer_ids:
            return
        self.temp_park_count += 1
        self.temp_park_events.append({
            'wafer_id': wafer.wafer_id,
            'from_location': wafer.current_location_id,
            'to_location': (
                wafer.assignment_queue[0].to_location
                if wafer.assignment_queue else ''
            ),
        })
        self.affected_wafer_ids.add(wafer.wafer_id)
        if wafer.storage_start_time is not None:
            self._record_storage_duration(wafer.wafer_id, time.time() - wafer.storage_start_time)

    def on_rerouted(self, count: int = 1):
        self.reroute_count += count

    def on_wafer_completed(self, wafer_id: int):
        self.completed_wafer_count += 1

    def ignore_wafer(self, wafer_id: int):
        self.ignored_wafer_ids.add(wafer_id)
        self.affected_wafer_ids.discard(wafer_id)
        self.storage_overruns.pop(wafer_id, None)

    def on_fault_candidates(self, chamber_id: str, wafer_ids):
        self.fault_replan_candidates[chamber_id] = list(wafer_ids)

    def on_fault_plan_decision(
            self, chamber_id: str, current_score, global_score, applied: bool, event: str = 'fault'):
        self.fault_plan_decisions.append({
            'event': event,
            'chamber_id': chamber_id,
            'current_score': list(current_score),
            'global_score': list(global_score),
            'applied': applied,
        })

    def on_timing_replan(
            self,
            wafer_id: int,
            hold_location: str,
            protected_chamber: str,
            delay: float,
            plan_change: dict = None):
        record = {
            'wafer_id': wafer_id,
            'hold_location': hold_location,
            'protected_chamber': protected_chamber,
            'delay': delay,
        }
        if plan_change:
            record.update(plan_change)
        self.timing_replans.append(record)

    def snapshot(self, event: str, chamber_id: str = None):
        record = copy.deepcopy(self.summary())
        record.pop('snapshots', None)
        record['event'] = event
        record['chamber_id'] = chamber_id
        self.snapshots.append(record)

    def refresh_open_storage(self, system):
        """Include wafers still waiting in a chamber at report time."""
        for wafer in system.wafers.values():
            if wafer.wafer_id in self.ignored_wafer_ids:
                continue
            if wafer.storage_start_time is None:
                continue
            if not is_chamber(wafer.current_location_id):
                continue
            storage_duration = time.time() - wafer.storage_start_time
            self._record_storage_duration(wafer.wafer_id, storage_duration)

    def summary(self) -> dict:
        makespan = 0.0
        if self.start_time:
            end = self.end_time or time.time()
            makespan = max(0.0, end - self.start_time)

        return {
            'time_scale': SystemConfig.TIME_SCALE,
            'storage_threshold': self.threshold,
            'affected_wafer_count': len(self.affected_wafer_ids),
            'affected_wafer_ids': sorted(self.affected_wafer_ids),
            'temp_park_count': self.temp_park_count,
            'temp_park_events': self.temp_park_events,
            'storage_overrun_events': self.storage_overrun_events,
            'max_storage_overrun': max(self.storage_overruns.values(), default=0.0),
            'total_storage_overrun': sum(self.storage_overruns.values()),
            'completed_wafer_count': self.completed_wafer_count,
            'reroute_count': self.reroute_count,
            'makespan': makespan,
            'fault_replan_candidates': self.fault_replan_candidates,
            'fault_plan_decisions': self.fault_plan_decisions,
            'timing_replans': self.timing_replans,
            'snapshots': self.snapshots,
        }

    def write_json(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.summary(), ensure_ascii=False, sort_keys=True, indent=2),
            encoding='utf-8',
        )

    def log_summary(self):
        summary = self.summary()
        logging.info("")
        logging.info("=" * 80)
        logging.info("PVD Fault Metrics")
        logging.info("=" * 80)
        logging.info(f"affected_wafer_count: {summary['affected_wafer_count']}")
        logging.info(f"time_scale: {summary['time_scale']}")
        logging.info(f"storage_threshold: {summary['storage_threshold']:.1f}s wall-clock")
        logging.info(f"affected_wafer_ids: {summary['affected_wafer_ids']}")
        logging.info(f"temp_park_count: {summary['temp_park_count']}")
        logging.info(f"max_storage_overrun: {summary['max_storage_overrun']:.1f}s")
        logging.info(f"total_storage_overrun: {summary['total_storage_overrun']:.1f}s")
        logging.info(f"completed_wafer_count: {summary['completed_wafer_count']}")
        logging.info(f"reroute_count: {summary['reroute_count']}")
        logging.info(f"makespan: {summary['makespan']:.1f}s")
        for chamber_id, wafer_ids in summary['fault_replan_candidates'].items():
            logging.info(f"fault_replan_candidates[{chamber_id}]: {wafer_ids}")
        logging.info("=" * 80)
        logging.info("")

    def _record_storage_duration(self, wafer_id: int, storage_duration: float):
        overrun = max(0.0, storage_duration - self.threshold)
        previous = self.storage_overruns.get(wafer_id, 0.0)
        if overrun > previous:
            self.storage_overruns[wafer_id] = overrun
        if overrun > 0:
            self.affected_wafer_ids.add(wafer_id)
