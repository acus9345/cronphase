"""
Multi-format parser for UNIX Crontab, Kubernetes CronJob YAML, and GitHub Actions workflows.
Supports standard 5-field cron expressions, @hourly/@daily/@weekly/@midnight macros,
and inline or heuristic job metadata (@duration, @weight, @window, @lock, @resource).
"""

from __future__ import annotations
import dataclasses
import re
from typing import Dict, List, Optional, Set, Tuple

MACROS: Dict[str, str] = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


@dataclasses.dataclass(frozen=True)
class CronExpression:
    raw: str
    minute_field: str
    hour_field: str
    dom_field: str
    month_field: str
    dow_field: str
    minutes: Tuple[int, ...]
    hours: Tuple[int, ...]
    doms: Tuple[int, ...]
    months: Tuple[int, ...]
    dows: Tuple[int, ...]
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expr: str) -> "CronExpression":
        cleaned = expr.strip()
        if cleaned.lower() in MACROS:
            cleaned = MACROS[cleaned.lower()]
        parts = cleaned.split()
        if len(parts) != 5:
            raise ValueError(f"Expected 5 cron fields, got {len(parts)} in '{expr}'")

        m_str, h_str, dom_str, mon_str, dow_str = parts
        minutes = tuple(sorted(_parse_field(m_str, 0, 59)))
        hours = tuple(sorted(_parse_field(h_str, 0, 23)))
        doms = tuple(sorted(_parse_field(dom_str, 1, 31)))
        months = tuple(sorted(_parse_field(mon_str, 1, 12, MONTH_NAMES)))
        dows_raw = _parse_field(dow_str, 0, 7, DOW_NAMES)
        dows = tuple(sorted({0 if d == 7 else d for d in dows_raw}))

        return cls(
            raw=expr.strip(),
            minute_field=m_str,
            hour_field=h_str,
            dom_field=dom_str,
            month_field=mon_str,
            dow_field=dow_str,
            minutes=minutes,
            hours=hours,
            doms=doms,
            months=months,
            dows=dows,
            dom_restricted=(dom_str != "*"),
            dow_restricted=(dow_str != "*"),
        )

    def matches_minute_of_day(
        self,
        minute_of_day: int,
        day_of_week: int = 0,
        day_of_month: int = 1,
        month: int = 1,
    ) -> bool:
        if month not in self.months:
            return False
        # Standard Vixie cron semantics: if both DOM and DOW are restricted (not '*'),
        # match if EITHER matches; otherwise both must match.
        if self.dom_restricted and self.dow_restricted:
            day_match = (day_of_month in self.doms) or (day_of_week in self.dows)
        else:
            day_match = (day_of_month in self.doms) and (day_of_week in self.dows)
        if not day_match:
            return False

        h = (minute_of_day // 60) % 24
        m = minute_of_day % 60
        return (h in self.hours) and (m in self.minutes)

    def firing_minutes_in_day(self) -> List[int]:
        """Returns sorted list of minutes [0..1439] in a representative active day."""
        out: List[int] = []
        for h in self.hours:
            base = h * 60
            for m in self.minutes:
                out.append(base + m)
        return out

    def format_canonical(self) -> str:
        return f"{self.minute_field} {self.hour_field} {self.dom_field} {self.month_field} {self.dow_field}"


def _parse_field(
    field: str,
    min_val: int,
    max_val: int,
    names: Optional[Dict[str, int]] = None,
) -> Set[int]:
    text = field.strip().lower()
    if names:
        for k, v in names.items():
            text = re.sub(rf"\b{k}\b", str(v), text)

    result: Set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Step must be positive in '{field}'")
        else:
            range_part = part

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            s_str, e_str = range_part.split("-", 1)
            start, end = int(s_str), int(e_str)
        else:
            val = int(range_part)
            if "/" in part:
                start, end = val, max_val
            else:
                start, end = val, val

        if start < min_val or end > max_val or start > end:
            raise ValueError(
                f"Field '{field}' value out of bounds [{min_val}..{max_val}]"
            )
        for v in range(start, end + 1, step):
            result.add(v)

    if not result:
        raise ValueError(f"Empty cron field '{field}'")
    return result


@dataclasses.dataclass
class ScheduledJob:
    id: str
    name: str
    cron: CronExpression
    command: str
    duration_min: int = 3
    weight: float = 2.0
    resource: str = "db-primary"
    locked: bool = False
    window_start_hour: Optional[int] = None
    window_end_hour: Optional[int] = None
    source_line: int = 0
    source_format: str = "crontab"  # 'crontab' | 'k8s' | 'github'


def infer_job_profile(name: str, command: str) -> Tuple[int, float, str]:
    """Heuristically estimates duration (minutes), load weight (1-10), and primary resource."""
    text = f"{name} {command}".lower()
    if any(k in text for k in ("pg_dump", "mysqldump", "backup", "snapshot", "xtrabackup")):
        return 12, 8.5, "db-replica"
    if any(k in text for k in ("vacuum", "reindex", "analyze", "optimize", "migration")):
        return 9, 9.0, "db-primary"
    if any(k in text for k in ("dbt", "warehouse", "snowflake", "bigquery", "spark", "etl", "rollup")):
        return 8, 7.0, "analytics-db"
    if any(k in text for k in ("billing", "invoice", "stripe", "ledger", "payout", "reconcile")):
        return 6, 6.5, "db-primary"
    if any(k in text for k in ("rsync", "s3", "rclone", "export", "sitemap", "feed")):
        return 5, 4.5, "storage-io"
    if any(k in text for k in ("pytest", "playwright", "cypress", "e2e", "audit", "trivy", "codeql")):
        return 7, 5.5, "ci-pool"
    if any(k in text for k in ("cache", "redis", "flush", "warm", "prune", "gc", "cleanup")):
        return 3, 4.0, "redis-cluster"
    if any(k in text for k in ("health", "ping", "heartbeat", "metrics", "exporter")):
        return 1, 1.2, "api-gateway"
    return 3, 3.0, "db-primary"


_CRONTAB_LINE_RE = re.compile(
    r"^\s*(?:(@(?:yearly|annually|monthly|weekly|daily|midnight|hourly))|"
    r"(\S+\s+\S+\s+\S+\s+\S+\s+\S+))\s+(.+?)$",
    re.IGNORECASE,
)


def _extract_annotations(text: str) -> Dict[str, str]:
    """Parses @duration=5m @weight=4 @resource=db @window=01-05 @lock tags from comments."""
    meta: Dict[str, str] = {}
    for m in re.finditer(r"@(duration|weight|resource|window|name|id)=([^\s#]+)", text):
        meta[m.group(1).lower()] = m.group(2)
    if re.search(r"@lock\b", text):
        meta["lock"] = "true"
    return meta


def parse_manifest(content: str) -> List[ScheduledJob]:
    """Auto-detects and parses UNIX crontab, Kubernetes CronJob YAML, or GitHub Actions YAML."""
    lines = content.splitlines()
    is_yaml = any(
        re.match(r"^\s*(apiVersion:|kind:\s*CronJob|on:|schedule:|-\s*cron:)", ln)
        for ln in lines
    )
    if is_yaml:
        return _parse_yaml_schedules(lines)
    return _parse_crontab(lines)


def _parse_crontab(lines: List[str]) -> List[ScheduledJob]:
    jobs: List[ScheduledJob] = []
    pending_comment = ""
    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            pending_comment = ""
            continue
        if stripped.startswith("#"):
            pending_comment += " " + stripped[1:].strip()
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", stripped):
            continue

        m = _CRONTAB_LINE_RE.match(stripped)
        if not m:
            continue
        expr_str = m.group(1) or m.group(2)
        rest = m.group(3).strip()

        if " #" in rest:
            cmd_part, inline_comment = rest.split(" #", 1)
        else:
            cmd_part, inline_comment = rest, ""

        tokens = cmd_part.split()
        if len(tokens) >= 2 and tokens[0] in ("root", "ubuntu", "postgres", "www-data", "app", "cron"):
            cmd_clean = " ".join(tokens[1:])
        else:
            cmd_clean = cmd_part.strip()

        full_comment = f"{pending_comment} {inline_comment}".strip()
        pending_comment = ""

        try:
            cron = CronExpression.parse(expr_str)
        except ValueError:
            continue

        meta = _extract_annotations(full_comment)
        job_id = f"job-{len(jobs) + 1:02d}"
        name = meta.get("name") or _derive_job_name(cmd_clean, full_comment, len(jobs) + 1)
        dur, weight, resource = infer_job_profile(name, cmd_clean)

        if "duration" in meta:
            dur_val = re.sub(r"[^0-9]", "", meta["duration"])
            if dur_val:
                dur = max(1, min(180, int(dur_val)))
        if "weight" in meta:
            try:
                weight = max(0.5, min(25.0, float(meta["weight"])))
            except ValueError:
                pass
        if "resource" in meta:
            resource = meta["resource"]

        win_start, win_end = None, None
        if "window" in meta:
            wm = re.match(r"(\d{1,2})(?::\d{2})?-(\d{1,2})(?::\d{2})?", meta["window"])
            if wm:
                win_start, win_end = int(wm.group(1)) % 24, int(wm.group(2)) % 24

        jobs.append(
            ScheduledJob(
                id=job_id,
                name=name,
                cron=cron,
                command=cmd_clean,
                duration_min=dur,
                weight=weight,
                resource=resource,
                locked=("lock" in meta),
                window_start_hour=win_start,
                window_end_hour=win_end,
                source_line=idx,
                source_format="crontab",
            )
        )
    return jobs


_YAML_NAME_RE = re.compile(r"""^name:\s*['"]?([A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)*)['"]?""")
_YAML_SCHED_RE = re.compile(
    r"""^(?:schedule|-\s*cron):\s*['"]?(@[a-zA-Z]+|[^'"#]+?)['"]?\s*(?:#(.*))?$"""
)


def _parse_yaml_schedules(lines: List[str]) -> List[ScheduledJob]:
    """Parses Kubernetes CronJob manifests and GitHub Actions workflow schedules."""
    jobs: List[ScheduledJob] = []
    current_name: Optional[str] = None
    current_cmd: str = ""
    current_comment: str = ""

    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            current_comment += " " + stripped[1:].strip()
            continue
        if stripped == "---":
            current_name = None
            current_cmd = ""
            current_comment = ""
            continue

        name_m = _YAML_NAME_RE.match(stripped)
        if name_m and not current_name:
            current_name = name_m.group(1).strip()

        sched_m = _YAML_SCHED_RE.match(stripped)
        if sched_m:
            expr_str = sched_m.group(1).strip()
            inline_c = (sched_m.group(2) or "").strip()
            fmt = "github" if stripped.startswith("-") or "cron:" in stripped else "k8s"
            try:
                cron = CronExpression.parse(expr_str)
            except ValueError:
                continue

            full_meta_text = f"{current_comment} {inline_c}".strip()
            meta = _extract_annotations(full_meta_text)
            job_idx = len(jobs) + 1
            j_name = meta.get("name") or current_name or f"{fmt}-job-{job_idx:02d}"
            dur, weight, resource = infer_job_profile(j_name, current_cmd or full_meta_text)
            if "duration" in meta:
                d_str = re.sub(r"[^0-9]", "", meta["duration"])
                if d_str:
                    dur = max(1, int(d_str))
            if "weight" in meta:
                try:
                    weight = float(meta["weight"])
                except ValueError:
                    pass
            if "resource" in meta:
                resource = meta["resource"]

            win_start, win_end = None, None
            if "window" in meta:
                wm = re.match(r"(\d{1,2})(?::\d{2})?-(\d{1,2})(?::\d{2})?", meta["window"])
                if wm:
                    win_start, win_end = int(wm.group(1)) % 24, int(wm.group(2)) % 24

            jobs.append(
                ScheduledJob(
                    id=f"job-{job_idx:02d}",
                    name=j_name,
                    cron=cron,
                    command=current_cmd or f"[{fmt.upper()}] {j_name}",
                    duration_min=dur,
                    weight=weight,
                    resource=resource,
                    locked=("lock" in meta),
                    window_start_hour=win_start,
                    window_end_hour=win_end,
                    source_line=idx,
                    source_format=fmt,
                )
            )
            if fmt == "k8s":
                current_name = None
            current_comment = ""
    return jobs


def _derive_job_name(command: str, comment: str, index: int) -> str:
    clean_c = re.sub(r"@[a-zA-Z0-9_=-]+", "", comment).strip(" -:#")
    if clean_c and len(clean_c) <= 36:
        return re.sub(r"\s+", "-", clean_c.lower())
    tokens = [
        t for t in command.split()
        if not t.startswith("-") and "=" not in t and t not in ("/bin/sh", "/bin/bash", "python", "python3", "node", "flock", "nice", "ionice", "cronic")
    ]
    if tokens:
        base = tokens[0].rsplit("/", 1)[-1]
        base = re.sub(r"\.(sh|py|js|rb|pl|sql)$", "", base)
        if len(tokens) > 1 and base in ("manage", "artisan", "rake", "celery", "kubectl", "pg_dump", "mysqldump", "dbt"):
            sub = re.sub(r"[^a-zA-Z0-9_-]", "", tokens[1])
            if sub:
                return f"{base}-{sub}"[:32]
        if base:
            return base[:32]
    return f"cron-job-{index:02d}"
