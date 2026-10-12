"""Per-fantasy-team cache for My Team roster + matchup schedule.

Switching teams in Settings must not lose the last Refresh for that team.
"""

from __future__ import annotations

from typing import Any

import config
from store import load_json, save_json, utc_now_iso


def _dir():
    path = config.DATA_DIR / "team_snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path(team_id: str | None):
    tid = str(team_id or "").strip()
    if not tid:
        return None
    return _dir() / f"{tid}.json"


def save_team_snapshot(
    team_id: str | None,
    *,
    my_team: dict[str, Any] | None = None,
    matchups: dict[str, Any] | None = None,
) -> bool:
    """Persist last-known roster/schedule for a fantasy team id."""
    path = snapshot_path(team_id)
    if path is None:
        return False
    tid = str(team_id).strip()
    team = dict(my_team or {})
    m = dict(matchups or {})
    slots = team.get("slots") or []
    filled = sum(1 for s in slots if s.get("player"))
    schedule = list(m.get("my_schedule") or [])
    # Skip empty snapshots so we don't overwrite a good cache with blanks.
    if not filled and not schedule and not (m.get("my_matchup") or {}).get("home"):
        existing = load_json(path, None)
        if existing:
            return False
    payload = {
        "team_id": tid,
        "saved_at": utc_now_iso(),
        "my_team": {
            **team,
            "team_id": tid,
        },
        "my_matchup": m.get("my_matchup") or {},
        "my_schedule": schedule,
        "my_schedule_team_id": tid,
        "week": m.get("week"),
    }
    save_json(path, payload)
    return True


def load_team_snapshot(team_id: str | None) -> dict[str, Any] | None:
    path = snapshot_path(team_id)
    if path is None or not path.exists():
        return None
    raw = load_json(path, None)
    if not isinstance(raw, dict):
        return None
    return raw


def snapshot_active_team() -> bool:
    """Save currently active my_team.json + matchups.json under their team id."""
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    tid = str(my_team.get("team_id") or matchups.get("my_schedule_team_id") or "").strip()
    if not tid:
        return False
    return save_team_snapshot(tid, my_team=my_team, matchups=matchups)
