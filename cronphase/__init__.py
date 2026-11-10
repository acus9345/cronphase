"""
cronphase — Fleet-wide cron thundering-herd detector & deterministic phase-shift solver.
"""

from .parser import CronExpression, ScheduledJob, parse_manifest
from .engine import SimulationResult, simulate_day
from .solver import SolverConfig, optimize_fleet
from .diff import generate_unified_diff, apply_rewrites

__version__ = "0.2.1"
__all__ = [
    "CronExpression",
    "ScheduledJob",
    "parse_manifest",
    "SimulationResult",
    "simulate_day",
    "SolverConfig",
    "optimize_fleet",
    "generate_unified_diff",
    "apply_rewrites",
]
