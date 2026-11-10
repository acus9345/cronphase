"""
Deterministic Constrained Phase-Shift Compiler for Cron Fleets.
Shifts job minute/hour phase offsets while strictly preserving:
  1. Exact execution cadence / period (every 5m/15m/30m, hourly, multi-hour lattice, daily)
  2. Pinned jobs (`locked=True`)
  3. Allowed maintenance window bounds (`window_start_hour`..`window_end_hour`)
  4. Standard POSIX/Vixie cron syntax compatibility
"""

from __future__ import annotations
import copy
import dataclasses
from typing import Dict, List, Optional, Tuple
from .parser import CronExpression, ScheduledJob
from .engine import SimulationResult, simulate_day


@dataclasses.dataclass
class SolverConfig:
    strategy: str = "anneal"  # 'anneal' | 'prime-splay' | 'fnv-hash'
    preserve_hours_for_daily: bool = False
    daily_window_hours: Tuple[int, int] = (0, 4)


@dataclasses.dataclass
class JobRewrite:
    job_id: str
    job_name: str
    old_expr: str
    new_expr: str
    phase_shift_minutes: int
    reason: str


def _detect_minute_period(minutes: Tuple[int, ...]) -> Optional[int]:
    """If minutes form an exact periodic lattice across [0..59], return its period."""
    n = len(minutes)
    if n == 1:
        return 60
    if n in (2, 3, 4, 5, 6, 10, 12, 15, 20, 30):
        period = 60 // n
        base = minutes[0] % period
        expected = tuple(sorted((base + i * period) % 60 for i in range(n)))
        if minutes == expected:
            return period
    return None


def _detect_hour_period(hours: Tuple[int, ...]) -> Optional[int]:
    """If hours form a multi-hour periodic lattice (e.g., */2, */3, */4, */6, */8, */12), return period."""
    n = len(hours)
    if n in (2, 3, 4, 6, 8, 12):
        period = 24 // n
        base = hours[0] % period
        expected = tuple(sorted((base + i * period) % 24 for i in range(n)))
        if hours == expected:
            return period
    return None


def _build_shifted_minute_field(period: int, phase: int) -> str:
    phase_mod = phase % period
    if period == 60:
        return str(phase_mod)
    if phase_mod == 0:
        return f"*/{period}"
    return f"{phase_mod}-59/{period}"


def _build_shifted_hour_field(h_period: int, h_phase: int) -> str:
    p = h_phase % h_period
    if p == 0:
        return f"*/{h_period}"
    return f"{p}-23/{h_period}"


def _allowed_hours_for_job(job: ScheduledJob, cfg: SolverConfig) -> List[int]:
    if job.window_start_hour is not None and job.window_end_hour is not None:
        s, e = job.window_start_hour, job.window_end_hour
        if s <= e:
            return list(range(s, e + 1))
        return list(range(s, 24)) + list(range(0, e + 1))

    if not cfg.preserve_hours_for_daily and len(job.cron.hours) == 1 and len(job.cron.minutes) == 1:
        orig_h = job.cron.hours[0]
        w_start, w_end = cfg.daily_window_hours
        if w_start <= orig_h <= w_end:
            return list(range(w_start, w_end + 1))
    return list(job.cron.hours)


def _fnv1a_32(text: str) -> int:
    h = 0x811C9DC5
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def optimize_fleet(
    jobs: List[ScheduledJob],
    config: Optional[SolverConfig] = None,
) -> Tuple[List[ScheduledJob], List[JobRewrite], SimulationResult, SimulationResult]:
    """
    Optimizes the fleet schedule to minimize Peak Load (L_inf) and Load Variance (L_2).
    Returns (optimized_jobs, rewrites, before_sim, after_sim).
    """
    cfg = config or SolverConfig()
    before_sim = simulate_day(jobs)
    optimized = [copy.deepcopy(j) for j in jobs]

    indexed_jobs = list(enumerate(optimized))
    indexed_jobs.sort(
        key=lambda pair: (
            not pair[1].locked,
            pair[1].weight * pair[1].duration_min * len(pair[1].cron.firing_minutes_in_day()),
        ),
        reverse=True,
    )

    if cfg.strategy == "fnv-hash":
        _apply_hash_splay(optimized, cfg)
    elif cfg.strategy == "prime-splay":
        _apply_prime_splay(optimized, cfg)
    else:
        _apply_minimax_solver(optimized, indexed_jobs, cfg)

    rewrites: List[JobRewrite] = []
    for orig, new_job in zip(jobs, optimized):
        old_s = orig.cron.format_canonical()
        new_s = new_job.cron.format_canonical()
        if old_s != new_s:
            old_first = orig.cron.firing_minutes_in_day()[0] if orig.cron.firing_minutes_in_day() else 0
            new_first = new_job.cron.firing_minutes_in_day()[0] if new_job.cron.firing_minutes_in_day() else 0
            delta = new_first - old_first
            rewrites.append(
                JobRewrite(
                    job_id=orig.id,
                    job_name=orig.name,
                    old_expr=orig.cron.raw,
                    new_expr=new_s,
                    phase_shift_minutes=delta,
                    reason=f"De-phased from {old_s} to flatten {orig.resource} peak contention",
                )
            )

    after_sim = simulate_day(optimized)
    return optimized, rewrites, before_sim, after_sim


def _candidate_expressions(job: ScheduledJob, cfg: SolverConfig) -> List[CronExpression]:
    period = _detect_minute_period(job.cron.minutes)
    if period is None or period <= 1:
        return [job.cron]

    allowed_hours = _allowed_hours_for_job(job, cfg)
    h_period = _detect_hour_period(job.cron.hours)
    candidates: List[CronExpression] = []

    if len(job.cron.hours) == 1 and len(allowed_hours) > 1 and period == 60:
        for h in allowed_hours:
            for m_phase in range(0, 60):
                expr_str = f"{m_phase} {h} {job.cron.dom_field} {job.cron.month_field} {job.cron.dow_field}"
                candidates.append(CronExpression.parse(expr_str))
    elif h_period is not None and period == 60:
        for hp in range(0, h_period):
            h_field = _build_shifted_hour_field(h_period, hp)
            for m_phase in range(0, 60):
                expr_str = f"{m_phase} {h_field} {job.cron.dom_field} {job.cron.month_field} {job.cron.dow_field}"
                candidates.append(CronExpression.parse(expr_str))
    else:
        for m_phase in range(0, period):
            m_field = _build_shifted_minute_field(period, m_phase)
            expr_str = f"{m_field} {job.cron.hour_field} {job.cron.dom_field} {job.cron.month_field} {job.cron.dow_field}"
            candidates.append(CronExpression.parse(expr_str))

    return candidates


def _apply_hash_splay(jobs: List[ScheduledJob], cfg: SolverConfig) -> None:
    for job in jobs:
        if job.locked:
            continue
        period = _detect_minute_period(job.cron.minutes)
        if not period or period <= 1:
            continue
        h_val = _fnv1a_32(f"{job.name}:{job.command}")
        allowed_hours = _allowed_hours_for_job(job, cfg)
        h_period = _detect_hour_period(job.cron.hours)
        m_phase = h_val % period
        m_field = _build_shifted_minute_field(period, m_phase)
        h_field = job.cron.hour_field
        if len(job.cron.hours) == 1 and len(allowed_hours) > 1 and period == 60:
            h_field = str(allowed_hours[(h_val >> 8) % len(allowed_hours)])
        elif h_period is not None and period == 60:
            h_field = _build_shifted_hour_field(h_period, (h_val >> 8) % h_period)
        job.cron = CronExpression.parse(
            f"{m_field} {h_field} {job.cron.dom_field} {job.cron.month_field} {job.cron.dow_field}"
        )


def _apply_prime_splay(jobs: List[ScheduledJob], cfg: SolverConfig) -> None:
    prime_stride = 17
    prime_hour_stride = 3
    for idx, job in enumerate(jobs):
        if job.locked:
            continue
        period = _detect_minute_period(job.cron.minutes)
        if not period or period <= 1:
            continue
        allowed_hours = _allowed_hours_for_job(job, cfg)
        h_period = _detect_hour_period(job.cron.hours)
        m_phase = ((idx + 1) * prime_stride) % period
        m_field = _build_shifted_minute_field(period, m_phase)
        h_field = job.cron.hour_field
        if len(job.cron.hours) == 1 and len(allowed_hours) > 1 and period == 60:
            h_field = str(allowed_hours[((idx + 1) * prime_hour_stride) % len(allowed_hours)])
        elif h_period is not None and period == 60:
            h_field = _build_shifted_hour_field(h_period, ((idx + 1) * prime_hour_stride) % h_period)
        job.cron = CronExpression.parse(
            f"{m_field} {h_field} {job.cron.dom_field} {job.cron.month_field} {job.cron.dow_field}"
        )


def _apply_minimax_solver(
    jobs: List[ScheduledJob],
    ordered_pairs: List[Tuple[int, ScheduledJob]],
    cfg: SolverConfig,
) -> None:
    """
    3-pass coordinate descent on the 1,440-minute load vector.
    Minimizes lexicographical objective:
      (candidate_peak_load, candidate_resource_peak, l2_sum_of_squares_delta, concurrency_l2_delta, harmonic_penalty)
    """
    total_load = [0.0] * 1440
    total_conc = [0] * 1440
    res_load: Dict[str, List[float]] = {}

    def add_job_to_grid(j: ScheduledJob, sign: int) -> None:
        r_arr = res_load.setdefault(j.resource, [0.0] * 1440)
        dur = max(1, j.duration_min)
        w = j.weight * sign
        visited = set()
        for s in j.cron.firing_minutes_in_day():
            for off in range(dur):
                m = (s + off) % 1440
                if m not in visited:
                    visited.add(m)
                    total_load[m] += w
                    total_conc[m] += sign
                    r_arr[m] += w

    for j in jobs:
        add_job_to_grid(j, +1)

    for _pass in range(3):
        improved_in_pass = False
        for idx, job in ordered_pairs:
            if job.locked:
                continue
            candidates = _candidate_expressions(job, cfg)
            if len(candidates) <= 1:
                continue

            add_job_to_grid(job, -1)
            r_arr = res_load[job.resource]
            dur = max(1, job.duration_min)
            w = job.weight

            best_cron = job.cron
            best_score: Optional[Tuple[float, float, float, float, int]] = None

            seed_offset = ((idx + 1) * 13) % len(candidates)
            rotated = candidates[seed_offset:] + candidates[:seed_offset]

            for cand in rotated:
                fires = cand.firing_minutes_in_day()
                cand_peak_load = 0.0
                cand_res_peak = 0.0
                delta_l2 = 0.0
                delta_conc_l2 = 0.0

                touched = {
                    (s + off) % 1440
                    for s in fires
                    for off in range(dur)
                }

                for m in touched:
                    nl = total_load[m] + w
                    nr = r_arr[m] + w
                    nc = total_conc[m] + 1
                    if nl > cand_peak_load:
                        cand_peak_load = nl
                    if nr > cand_res_peak:
                        cand_res_peak = nr
                    delta_l2 += nl * nl - total_load[m] * total_load[m]
                    delta_conc_l2 += nc * nc - total_conc[m] * total_conc[m]

                first_mod = (fires[0] % 60) if fires else 0
                harmonic_penalty = 2 if first_mod == 0 else (1 if first_mod % 15 == 0 else 0)

                score = (
                    round(cand_peak_load, 4),
                    round(cand_res_peak, 4),
                    round(delta_l2, 4),
                    round(delta_conc_l2, 4),
                    harmonic_penalty,
                )
                if best_score is None or score < best_score:
                    best_score = score
                    best_cron = cand

            if best_cron.format_canonical() != job.cron.format_canonical():
                improved_in_pass = True
            job.cron = best_cron
            add_job_to_grid(job, +1)

        if not improved_in_pass:
            break
