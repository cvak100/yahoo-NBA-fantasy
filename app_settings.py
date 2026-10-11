"""Persisted local app settings (daily refresh, etc.)."""

from __future__ import annotations

from typing import Any

import config
from store import load_json, save_json


REFRESH_STEP_KEYS = (
    "league",
    "roster",
    "news",
    "injuries",
    "trends",
    "available",
)

REFRESH_STEP_LABELS = {
    "league": "League data (settings, standings, matchups)",
    "roster": "My roster players",
    "news": "Fantasy news (NBA + CBS)",
    "injuries": "Injury report",
    "trends": "Roster / transaction trends",
    "available": "Top available players",
}

DEFAULTS: dict[str, Any] = {
    "refresh_available_count": config.DEFAULT_REFRESH_AVAILABLE_COUNT,
    "my_team_id": None,
    "refresh_steps": {key: True for key in REFRESH_STEP_KEYS},
}


def _clamp_available_count(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = config.DEFAULT_REFRESH_AVAILABLE_COUNT
    return max(
        config.MIN_REFRESH_AVAILABLE_COUNT,
        min(config.MAX_REFRESH_AVAILABLE_COUNT, n),
    )


def _normalize_refresh_steps(raw: Any, *, legacy_include_roster: Any = None) -> dict[str, bool]:
    base = {key: True for key in REFRESH_STEP_KEYS}
    if isinstance(raw, dict):
        for key in REFRESH_STEP_KEYS:
            if key in raw:
                base[key] = bool(raw[key])
    elif legacy_include_roster is not None:
        # Older settings only had include_my_roster.
        base["roster"] = bool(legacy_include_roster)
    return base


def load_app_settings() -> dict[str, Any]:
    raw = load_json(config.APP_SETTINGS_PATH, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    settings = {**DEFAULTS, **raw}
    settings["refresh_available_count"] = _clamp_available_count(
        settings.get("refresh_available_count")
    )
    settings["refresh_steps"] = _normalize_refresh_steps(
        raw.get("refresh_steps"),
        legacy_include_roster=raw.get("include_my_roster"),
    )
    # Keep legacy key in sync for any older readers.
    settings["include_my_roster"] = bool(settings["refresh_steps"].get("roster", True))
    tid = settings.get("my_team_id")
    settings["my_team_id"] = str(tid).strip() if tid not in (None, "") else None
    return settings


def save_app_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    current = load_app_settings()
    incoming = payload if isinstance(payload, dict) else {}
    if "refresh_available_count" in incoming:
        current["refresh_available_count"] = _clamp_available_count(
            incoming.get("refresh_available_count")
        )
    if "my_team_id" in incoming:
        tid = incoming.get("my_team_id")
        current["my_team_id"] = (
            str(tid).strip() if tid not in (None, "") else None
        )
    if "refresh_steps" in incoming and isinstance(incoming.get("refresh_steps"), dict):
        current["refresh_steps"] = _normalize_refresh_steps(
            {**current.get("refresh_steps", {}), **incoming["refresh_steps"]}
        )
    elif "include_my_roster" in incoming:
        steps = dict(current.get("refresh_steps") or {})
        steps["roster"] = bool(incoming.get("include_my_roster"))
        current["refresh_steps"] = _normalize_refresh_steps(steps)

    current["include_my_roster"] = bool(
        (current.get("refresh_steps") or {}).get("roster", True)
    )
    # Persist without duplicating DEFAULTS noise beyond what we use.
    save_json(
        config.APP_SETTINGS_PATH,
        {
            "refresh_available_count": current["refresh_available_count"],
            "my_team_id": current["my_team_id"],
            "refresh_steps": current["refresh_steps"],
            "include_my_roster": current["include_my_roster"],
        },
    )
    return load_app_settings()


def resolve_my_team_id(league_my_team_id: str | None = None) -> str | None:
    """Prefer Settings override, else Yahoo-detected team id."""
    settings = load_app_settings()
    return settings.get("my_team_id") or (
        str(league_my_team_id) if league_my_team_id else None
    )


def refresh_step_enabled(settings: dict[str, Any] | None, key: str) -> bool:
    steps = (settings or {}).get("refresh_steps") or {}
    return bool(steps.get(key, True))
