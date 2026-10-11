"""Rolling history of main-refresh step durations for ETA / avg wait."""

from __future__ import annotations

from typing import Any

import config
from store import load_json, save_json, utc_now_iso

MAX_RUNS = 25
STEP_KEYS = ("league", "roster", "news", "injuries", "trends", "available")


def _empty() -> dict[str, Any]:
    return {"runs": [], "avg": {"total_sec": None, "steps": {}, "sample_count": 0}}


def load_refresh_timings() -> dict[str, Any]:
    raw = load_json(config.REFRESH_TIMINGS_PATH, None)
    if not isinstance(raw, dict):
        return _empty()
    runs = raw.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {"runs": runs, "avg": _compute_avg(runs)}


def _compute_avg(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        return {"total_sec": None, "steps": {}, "sample_count": 0}

    totals: list[float] = []
    by_step: dict[str, list[float]] = {k: [] for k in STEP_KEYS}

    for run in runs:
        try:
            t = float(run.get("total_sec"))
        except (TypeError, ValueError):
            t = None
        if t is not None and t >= 0:
            totals.append(t)
        steps = run.get("steps") or {}
        if not isinstance(steps, dict):
            continue
        for key in STEP_KEYS:
            try:
                sec = float(steps.get(key))
            except (TypeError, ValueError):
                continue
            if sec >= 0:
                by_step[key].append(sec)

    def mean(vals: list[float]) -> float | None:
        if not vals:
            return None
        return round(sum(vals) / len(vals), 1)

    return {
        "total_sec": mean(totals),
        "steps": {k: mean(v) for k, v in by_step.items() if v},
        "sample_count": len(runs),
    }


def record_refresh_run(
    *,
    step_seconds: dict[str, float],
    total_sec: float | None = None,
) -> dict[str, Any]:
    """Append one completed run and recompute averages."""
    data = load_refresh_timings()
    clean_steps: dict[str, float] = {}
    for key, val in (step_seconds or {}).items():
        try:
            sec = float(val)
        except (TypeError, ValueError):
            continue
        if sec >= 0:
            clean_steps[str(key)] = round(sec, 2)

    if total_sec is None:
        total_sec = round(sum(clean_steps.values()), 2)
    else:
        try:
            total_sec = round(float(total_sec), 2)
        except (TypeError, ValueError):
            total_sec = round(sum(clean_steps.values()), 2)

    run = {
        "at": utc_now_iso(),
        "total_sec": total_sec,
        "steps": clean_steps,
    }
    runs = list(data.get("runs") or [])
    runs.append(run)
    runs = runs[-MAX_RUNS:]
    avg = _compute_avg(runs)
    payload = {"runs": runs, "avg": avg}
    save_json(config.REFRESH_TIMINGS_PATH, payload)
    return payload


def avg_for_steps() -> dict[str, float]:
    avg = load_refresh_timings().get("avg") or {}
    steps = avg.get("steps") or {}
    return {str(k): float(v) for k, v in steps.items() if v is not None}


def avg_total_sec() -> float | None:
    avg = load_refresh_timings().get("avg") or {}
    val = avg.get("total_sec")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
