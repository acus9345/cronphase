"""
Command-Line Interface for `cronphase`.
Usage:
  python3 -m cronphase examples/prod.crontab --optimize --diff
"""

from __future__ import annotations
import argparse
import json
import sys
from typing import List, Optional
from .parser import parse_manifest
from .engine import format_hhmm, simulate_day
from .solver import SolverConfig, optimize_fleet
from .diff import apply_rewrites, generate_unified_diff


def _ascii_sparkline(values: List[float], width: int = 48) -> str:
    blocks = " ▂▃▄▅▆▇█"
    if not values:
        return ""
    step = max(1, len(values) // width)
    bucketed = [max(values[i : i + step]) for i in range(0, len(values), step)][:width]
    hi = max(bucketed) if bucketed else 1.0
    if hi <= 0:
        return blocks[0] * len(bucketed)
    return "".join(blocks[min(len(blocks) - 1, int((v / hi) * (len(blocks) - 1)))] for v in bucketed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cronphase",
        description="Fleet-wide cron thundering-herd analyzer & deterministic phase-shift compiler.",
    )
    parser.add_argument("manifest", help="Path to crontab, Kubernetes CronJob YAML, or GitHub Actions YAML ('-' for stdin)")
    parser.add_argument("--optimize", "-o", action="store_true", help="Run constrained phase-shift solver")
    parser.add_argument(
        "--strategy",
        choices=["anneal", "prime-splay", "fnv-hash"],
        default="anneal",
        help="Phase-shift solver algorithm (default: anneal)",
    )
    parser.add_argument("--diff", "-d", action="store_true", help="Output unified git diff patch")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    parser.add_argument(
        "--max-crest-factor",
        type=float,
        default=None,
        help="Exit with code 2 if Crest Factor (Peak/Mean load) exceeds threshold (for CI linting)",
    )

    args = parser.parse_args(argv)
    if args.manifest == "-":
        raw = sys.stdin.read()
        fname = "stdin.crontab"
    else:
        with open(args.manifest, "r", encoding="utf-8") as f:
            raw = f.read()
        fname = args.manifest.rsplit("/", 1)[-1]

    jobs = parse_manifest(raw)
    if not jobs:
        print("cronphase: no valid cron schedules found in input.", file=sys.stderr)
        return 1

    cfg = SolverConfig(strategy=args.strategy)
    if args.optimize or args.diff:
        opt_jobs, rewrites, before_sim, after_sim = optimize_fleet(jobs, cfg)
    else:
        before_sim = simulate_day(jobs)
        opt_jobs, rewrites, after_sim = jobs, [], before_sim

    if args.json:
        payload = {
            "job_count": len(jobs),
            "before": {
                "peak_concurrency": before_sim.peak_concurrency,
                "peak_load": before_sim.peak_load,
                "peak_time": format_hhmm(before_sim.peak_minute),
                "mean_load": before_sim.mean_load,
                "crest_factor": before_sim.crest_factor,
                "collision_minutes": before_sim.collision_minutes_count,
                "harmonics": before_sim.harmonic_buckets,
            },
            "after": {
                "peak_concurrency": after_sim.peak_concurrency,
                "peak_load": after_sim.peak_load,
                "peak_time": format_hhmm(after_sim.peak_minute),
                "mean_load": after_sim.mean_load,
                "crest_factor": after_sim.crest_factor,
                "collision_minutes": after_sim.collision_minutes_count,
                "harmonics": after_sim.harmonic_buckets,
            },
            "rewrites": [
                {
                    "job_id": r.job_id,
                    "job_name": r.job_name,
                    "old_expr": r.old_expr,
                    "new_expr": r.new_expr,
                    "phase_shift_minutes": r.phase_shift_minutes,
                }
                for r in rewrites
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"CRONPHASE // FLEET HARMONIC & COLLISION REPORT ({len(jobs)} jobs parsed)")
        print("=" * 74)
        b_loads = [s.load for s in before_sim.timeline]
        print(
            f"BASELINE   | Peak Load: {before_sim.peak_load:5.1f} ({before_sim.peak_concurrency} jobs @ {format_hhmm(before_sim.peak_minute)}) "
            f"| Crest: {before_sim.crest_factor:4.2f}x | Collisions: {before_sim.collision_minutes_count}m"
        )
        print(f"WAVEFORM   | [{_ascii_sparkline(b_loads, 48)}] (00:00..23:59)")
        if args.optimize or args.diff:
            a_loads = [s.load for s in after_sim.timeline]
            drop_pct = ((before_sim.peak_load - after_sim.peak_load) / before_sim.peak_load * 100.0) if before_sim.peak_load else 0.0
            print(
                f"OPTIMIZED  | Peak Load: {after_sim.peak_load:5.1f} ({after_sim.peak_concurrency} jobs @ {format_hhmm(after_sim.peak_minute)}) "
                f"| Crest: {after_sim.crest_factor:4.2f}x | Peak Drop: -{drop_pct:.1f}%"
            )
            print(f"DE-PHASED  | [{_ascii_sparkline(a_loads, 48)}] ({len(rewrites)} schedules shifted)")

        if args.diff and rewrites:
            print("\n" + "-" * 74)
            rewritten_text = apply_rewrites(raw, jobs, rewrites)
            print(generate_unified_diff(raw, rewritten_text, filename=fname).rstrip())

    active_sim = after_sim if (args.optimize or args.diff) else before_sim
    if args.max_crest_factor is not None and active_sim.crest_factor > args.max_crest_factor:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
