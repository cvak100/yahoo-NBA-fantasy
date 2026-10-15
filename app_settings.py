"""Persisted local app settings (refresh, analysis, chrome, etc.)."""

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

FREE_SCORE_WEIGHT_KEYS = ("l7_stats", "l14_stats", "adds", "rank_l7")
FA_CAT_KEYS = (
    "fg_pct",
    "ft_pct",
    "threes",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "tov",
)

DEFAULT_FREE_SCORE_WEIGHTS = {
    "l7_stats": 1.0,
    "l14_stats": 1.0,
    "adds": 1.0,
    "rank_l7": 1.0,
}

DEFAULT_FA_WEIGHTS = {
    "fg_pct": 0.8,
    "ft_pct": 0.8,
    "threes": 1.0,
    "pts": 0.6,
    "reb": 1.0,
    "ast": 0.8,
    "stl": 1.0,
    "blk": 1.0,
    "tov": 0.5,
}

DEFAULTS: dict[str, Any] = {
    "refresh_available_count": config.DEFAULT_REFRESH_AVAILABLE_COUNT,
    "my_team_id": None,
    "refresh_steps": {key: True for key in REFRESH_STEP_KEYS},
    # Free Players analysis
    "free_players_pool": 100,
    "free_players_top": 10,
    "free_players_score_weights": dict(DEFAULT_FREE_SCORE_WEIGHTS),
    # Decisions / FA ranking
    "streaming_top_n": 10,
    "fa_ranking_limit": 25,
    "fa_default_weights": dict(DEFAULT_FA_WEIGHTS),
    # Analytics
    "analytics_top_n": 5,
    # Connection / scrape
    "league_id": None,
    "chrome_debugger_address": config.CHROME_DEBUGGER_ADDRESS,
    "page_load_wait_sec": float(config.PAGE_LOAD_WAIT_SEC),
}


def _clamp_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _clamp_float(value: Any, *, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _clamp_available_count(value: Any) -> int:
    return _clamp_int(
        value,
        default=config.DEFAULT_REFRESH_AVAILABLE_COUNT,
        lo=config.MIN_REFRESH_AVAILABLE_COUNT,
        hi=config.MAX_REFRESH_AVAILABLE_COUNT,
    )


def _normalize_refresh_steps(raw: Any, *, legacy_include_roster: Any = None) -> dict[str, bool]:
    base = {key: True for key in REFRESH_STEP_KEYS}
    if isinstance(raw, dict):
        for key in REFRESH_STEP_KEYS:
            if key in raw:
                base[key] = bool(raw[key])
    elif legacy_include_roster is not None:
        base["roster"] = bool(legacy_include_roster)
    return base


def _normalize_weights(
    raw: Any,
    *,
    keys: tuple[str, ...],
    defaults: dict[str, float],
    lo: float = 0.0,
    hi: float = 10.0,
) -> dict[str, float]:
    out = {k: float(defaults.get(k, 1.0)) for k in keys}
    if not isinstance(raw, dict):
        return out
    for key in keys:
        if key not in raw:
            continue
        out[key] = _clamp_float(raw.get(key), default=out[key], lo=lo, hi=hi)
    return out


def _normalize_league_id(value: Any) -> str | None:
    s = str(value or "").strip()
    if not s or s.upper() == "YOUR_LEAGUE_ID":
        return None
    return s


def _normalize_debugger(value: Any) -> str:
    s = str(value or "").strip() or config.CHROME_DEBUGGER_ADDRESS
    return s


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
    settings["include_my_roster"] = bool(settings["refresh_steps"].get("roster", True))
    tid = settings.get("my_team_id")
    settings["my_team_id"] = str(tid).strip() if tid not in (None, "") else None

    settings["free_players_pool"] = _clamp_int(
        settings.get("free_players_pool"), default=100, lo=25, hi=200
    )
    settings["free_players_top"] = _clamp_int(
        settings.get("free_players_top"), default=10, lo=1, hi=50
    )
    settings["free_players_score_weights"] = _normalize_weights(
        settings.get("free_players_score_weights"),
        keys=FREE_SCORE_WEIGHT_KEYS,
        defaults=DEFAULT_FREE_SCORE_WEIGHTS,
    )

    settings["streaming_top_n"] = _clamp_int(
        settings.get("streaming_top_n"), default=10, lo=1, hi=50
    )
    settings["fa_ranking_limit"] = _clamp_int(
        settings.get("fa_ranking_limit"), default=25, lo=5, hi=100
    )
    settings["fa_default_weights"] = _normalize_weights(
        settings.get("fa_default_weights"),
        keys=FA_CAT_KEYS,
        defaults=DEFAULT_FA_WEIGHTS,
        lo=0.0,
        hi=5.0,
    )

    settings["analytics_top_n"] = _clamp_int(
        settings.get("analytics_top_n"), default=5, lo=3, hi=15
    )

    settings["league_id"] = _normalize_league_id(settings.get("league_id"))
    settings["chrome_debugger_address"] = _normalize_debugger(
        settings.get("chrome_debugger_address")
    )
    settings["page_load_wait_sec"] = _clamp_float(
        settings.get("page_load_wait_sec"),
        default=float(config.PAGE_LOAD_WAIT_SEC),
        lo=1.0,
        hi=30.0,
    )
    return settings


def _persistable(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "refresh_available_count": settings["refresh_available_count"],
        "my_team_id": settings["my_team_id"],
        "refresh_steps": settings["refresh_steps"],
        "include_my_roster": settings["include_my_roster"],
        "free_players_pool": settings["free_players_pool"],
        "free_players_top": settings["free_players_top"],
        "free_players_score_weights": settings["free_players_score_weights"],
        "streaming_top_n": settings["streaming_top_n"],
        "fa_ranking_limit": settings["fa_ranking_limit"],
        "fa_default_weights": settings["fa_default_weights"],
        "analytics_top_n": settings["analytics_top_n"],
        "league_id": settings["league_id"],
        "chrome_debugger_address": settings["chrome_debugger_address"],
        "page_load_wait_sec": settings["page_load_wait_sec"],
    }


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

    if "free_players_pool" in incoming:
        current["free_players_pool"] = _clamp_int(
            incoming.get("free_players_pool"), default=100, lo=25, hi=200
        )
    if "free_players_top" in incoming:
        current["free_players_top"] = _clamp_int(
            incoming.get("free_players_top"), default=10, lo=1, hi=50
        )
    if "free_players_score_weights" in incoming:
        current["free_players_score_weights"] = _normalize_weights(
            {
                **(current.get("free_players_score_weights") or {}),
                **(incoming.get("free_players_score_weights") or {}),
            },
            keys=FREE_SCORE_WEIGHT_KEYS,
            defaults=DEFAULT_FREE_SCORE_WEIGHTS,
        )

    if "streaming_top_n" in incoming:
        current["streaming_top_n"] = _clamp_int(
            incoming.get("streaming_top_n"), default=10, lo=1, hi=50
        )
    if "fa_ranking_limit" in incoming:
        current["fa_ranking_limit"] = _clamp_int(
            incoming.get("fa_ranking_limit"), default=25, lo=5, hi=100
        )
    if "fa_default_weights" in incoming:
        current["fa_default_weights"] = _normalize_weights(
            {
                **(current.get("fa_default_weights") or {}),
                **(incoming.get("fa_default_weights") or {}),
            },
            keys=FA_CAT_KEYS,
            defaults=DEFAULT_FA_WEIGHTS,
            lo=0.0,
            hi=5.0,
        )

    if "analytics_top_n" in incoming:
        current["analytics_top_n"] = _clamp_int(
            incoming.get("analytics_top_n"), default=5, lo=3, hi=15
        )

    if "league_id" in incoming:
        current["league_id"] = _normalize_league_id(incoming.get("league_id"))
    if "chrome_debugger_address" in incoming:
        current["chrome_debugger_address"] = _normalize_debugger(
            incoming.get("chrome_debugger_address")
        )
    if "page_load_wait_sec" in incoming:
        current["page_load_wait_sec"] = _clamp_float(
            incoming.get("page_load_wait_sec"),
            default=float(config.PAGE_LOAD_WAIT_SEC),
            lo=1.0,
            hi=30.0,
        )

    current["include_my_roster"] = bool(
        (current.get("refresh_steps") or {}).get("roster", True)
    )
    # Keep free top within pool.
    current["free_players_top"] = min(
        current["free_players_top"], current["free_players_pool"]
    )

    save_json(config.APP_SETTINGS_PATH, _persistable(current))
    settings = load_app_settings()
    apply_runtime_config(settings)
    return settings


def apply_runtime_config(settings: dict[str, Any] | None = None) -> None:
    """Push connection settings onto ``config`` for the running process."""
    s = settings if isinstance(settings, dict) else load_app_settings()
    lid = s.get("league_id") or None
    if lid:
        config.LEAGUE_ID = str(lid).strip()
        config.LEAGUE_URL = (
            f"https://basketball.fantasysports.yahoo.com/nba/{config.LEAGUE_ID}"
        )
        config.SETTINGS_URL = f"{config.LEAGUE_URL}/settings"
    dbg = str(s.get("chrome_debugger_address") or "").strip()
    if dbg:
        config.CHROME_DEBUGGER_ADDRESS = dbg
    try:
        wait = float(s.get("page_load_wait_sec"))
        if wait > 0:
            config.PAGE_LOAD_WAIT_SEC = wait
    except (TypeError, ValueError):
        pass


def resolve_my_team_id(league_my_team_id: str | None = None) -> str | None:
    """Prefer Settings override, else Yahoo-detected team id."""
    settings = load_app_settings()
    return settings.get("my_team_id") or (
        str(league_my_team_id) if league_my_team_id else None
    )


def refresh_step_enabled(settings: dict[str, Any] | None, key: str) -> bool:
    steps = (settings or {}).get("refresh_steps") or {}
    return bool(steps.get(key, True))
