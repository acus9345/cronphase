"""
Unified Diff & Rewritten Manifest Generator for Crontab, Kubernetes YAML, and GitHub Actions.
"""

from __future__ import annotations
import difflib
from typing import Dict, List
from .parser import ScheduledJob
from .solver import JobRewrite


def apply_rewrites(original_text: str, jobs: List[ScheduledJob], rewrites: List[JobRewrite]) -> str:
    """Applies phase-shifted cron expressions back to the original manifest line-by-line."""
    rewrite_by_id: Dict[str, JobRewrite] = {r.job_id: r for r in rewrites}
    line_to_rewrite: Dict[int, JobRewrite] = {}
    for j in jobs:
        if j.id in rewrite_by_id and j.source_line > 0:
            line_to_rewrite[j.source_line] = rewrite_by_id[j.id]

    out_lines: List[str] = []
    for idx, line in enumerate(original_text.splitlines(keepends=True), start=1):
        rw = line_to_rewrite.get(idx)
        if rw is not None:
            # Replace only inside the schedule portion before any trailing comment
            comment_idx = line.find(" #")
            if comment_idx != -1:
                head = line[:comment_idx].replace(rw.old_expr, rw.new_expr, 1)
                replaced = head + line[comment_idx:]
            else:
                replaced = line.replace(rw.old_expr, rw.new_expr, 1)
            out_lines.append(replaced)
        else:
            out_lines.append(line)
    return "".join(out_lines)


def generate_unified_diff(
    original_text: str,
    rewritten_text: str,
    filename: str = "crontab",
) -> str:
    orig_lines = [ln if ln.endswith("\n") else ln + "\n" for ln in original_text.splitlines()]
    new_lines = [ln if ln.endswith("\n") else ln + "\n" for ln in rewritten_text.splitlines()]
    diff = difflib.unified_diff(
        orig_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="\n",
    )
    return "".join(diff)
