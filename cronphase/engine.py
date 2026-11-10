"""
1,440-Minute Diurnal Concurrency, Resource Load & Harmonic Spectrum Simulator.
Accurately models multi-minute job durations, midnight wrap-around spillover,
crest factor (Peak / Mean), and pairwise job collision matrices.
"""

from __future__ import annotations
import dataclasses
import math
from typing import Dict, List, Tuple
from .parser import ScheduledJob


@dataclasses.dataclass
class MinuteSlice:
    minute: int  # 0..1439
    concurrency: int
    load: float
    active_job_ids: List[str]
    resource_loads: Dict[str, float]


@dataclasses.dataclass
class CollisionSpike:
    minute: int
    time_label: str
    concurrency: int
    total_load: float
    dominant_resource: str
    job_names: List[str]


@dataclasses.dataclass
class SimulationResult:
    timeline: List[MinuteSlice]
    peak_concurrency: int
    peak_load: float
    peak_minute: int
    mean_load: float
    p95_load: float
    l2_energy: float
    crest_factor: float
    collision_minutes_count: int
    harmonic_buckets: Dict[str, int]
    top_spikes: List[CollisionSpike]
    pairwise_overlaps: Dict[Tuple[str, str], int]


def format_hhmm(minute: int) -> str:
    m = minute % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def simulate_day(jobs: List[ScheduledJob], collision_threshold: int = 3) -> SimulationResult:
    timeline: List[MinuteSlice] = [
        MinuteSlice(minute=m, concurrency=0, load=0.0, active_job_ids=[], resource_loads={})
        for m in range(1440)
    ]

    harmonic_buckets: Dict[str, int] = {
        ":00": 0,
        ":15": 0,
        ":30": 0,
        ":45": 0,
        "5m-grid": 0,
        "prime/off-grid": 0,
    }

    for job in jobs:
        starts = job.cron.firing_minutes_in_day()
        dur = max(1, job.duration_min)
        for start_min in starts:
            mod_m = start_min % 60
            if mod_m == 0:
                harmonic_buckets[":00"] += 1
            elif mod_m == 15:
                harmonic_buckets[":15"] += 1
            elif mod_m == 30:
                harmonic_buckets[":30"] += 1
            elif mod_m == 45:
                harmonic_buckets[":45"] += 1
            elif mod_m % 5 == 0:
                harmonic_buckets["5m-grid"] += 1
            else:
                harmonic_buckets["prime/off-grid"] += 1

            for offset in range(dur):
                m_idx = (start_min + offset) % 1440
                sl = timeline[m_idx]
                if job.id not in sl.active_job_ids:
                    sl.active_job_ids.append(job.id)
                    sl.concurrency += 1
                    sl.load = round(sl.load + job.weight, 4)
                    sl.resource_loads[job.resource] = round(
                        sl.resource_loads.get(job.resource, 0.0) + job.weight, 4
                    )

    job_map = {j.id: j for j in jobs}
    loads = [sl.load for sl in timeline]
    concurrencies = [sl.concurrency for sl in timeline]

    peak_load = max(loads) if loads else 0.0
    peak_concurrency = max(concurrencies) if concurrencies else 0
    peak_minute = 0
    for sl in timeline:
        if sl.load == peak_load:
            peak_minute = sl.minute
            break

    mean_load = sum(loads) / 1440.0 if loads else 0.0
    sorted_loads = sorted(loads)
    p95_load = sorted_loads[int(1440 * 0.95)] if sorted_loads else 0.0
    l2_energy = math.sqrt(sum(x * x for x in loads) / 1440.0) if loads else 0.0
    crest_factor = (peak_load / mean_load) if mean_load > 1e-6 else 1.0
    collision_minutes_count = sum(1 for sl in timeline if sl.concurrency >= collision_threshold)

    pairwise: Dict[Tuple[str, str], int] = {}
    for sl in timeline:
        active = sl.active_job_ids
        n = len(active)
        if n > 1:
            for i in range(n):
                for k in range(i + 1, n):
                    pair = (active[i], active[k]) if active[i] < active[k] else (active[k], active[i])
                    pairwise[pair] = pairwise.get(pair, 0) + 1

    candidate_slices = sorted(
        [sl for sl in timeline if sl.concurrency >= 2],
        key=lambda s: (s.load, s.concurrency, -s.minute),
        reverse=True,
    )
    top_spikes: List[CollisionSpike] = []
    used_minutes: List[int] = []
    for sl in candidate_slices:
        if any(min((sl.minute - u) % 1440, (u - sl.minute) % 1440) < 5 for u in used_minutes):
            continue
        used_minutes.append(sl.minute)
        dom_res = "none"
        if sl.resource_loads:
            dom_res = max(sl.resource_loads.items(), key=lambda kv: kv[1])[0]
        top_spikes.append(
            CollisionSpike(
                minute=sl.minute,
                time_label=format_hhmm(sl.minute),
                concurrency=sl.concurrency,
                total_load=round(sl.load, 2),
                dominant_resource=dom_res,
                job_names=[job_map[jid].name for jid in sl.active_job_ids],
            )
        )
        if len(top_spikes) >= 8:
            break

    return SimulationResult(
        timeline=timeline,
        peak_concurrency=peak_concurrency,
        peak_load=round(peak_load, 2),
        peak_minute=peak_minute,
        mean_load=round(mean_load, 2),
        p95_load=round(p95_load, 2),
        l2_energy=round(l2_energy, 2),
        crest_factor=round(crest_factor, 2),
        collision_minutes_count=collision_minutes_count,
        harmonic_buckets=harmonic_buckets,
        top_spikes=top_spikes,
        pairwise_overlaps=pairwise,
    )
