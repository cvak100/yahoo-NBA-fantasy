"""Fetch NBA scoreboard (ESPN) for roster lockout weeks."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

from nba_teams import resolve_team

_ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
_CACHE: dict[str, Any] = {"key": None, "at": 0.0, "data": None}
_FIRST_MATCH_CACHE: dict[str, Any] = {"key": None, "at": 0.0, "date": None}
_CACHE_TTL_SEC = 300.0
_FIRST_MATCH_TTL_SEC = 6 * 3600.0


def _http_json(url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "yahoonba/1.0 (fantasy roster helper)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _abbr(raw: str | None) -> str | None:
    info = resolve_team(raw)
    if info:
        return str(info["abbr"])
    s = str(raw or "").strip().upper()
    return s or None


def _parse_events(payload: dict[str, Any], *, day: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in payload.get("events") or []:
        comps = (ev.get("competitions") or [{}])[0]
        teams = comps.get("competitors") or []
        home = away = None
        known = 0
        for t in teams:
            ab = _abbr((t.get("team") or {}).get("abbreviation"))
            if ab:
                known += 1
            if (t.get("homeAway") or "") == "home":
                home = ab
            else:
                away = ab
        # Skip exhibitions / non-NBA sides (e.g. London Lions).
        if known < 2 or not home or not away:
            continue
        status = ((ev.get("status") or {}).get("type") or {})
        out.append(
            {
                "date": day.isoformat(),
                "home": home,
                "away": away,
                "name": ev.get("name") or "",
                "start": ev.get("date") or comps.get("date"),
                "state": status.get("state") or "",
                "status": status.get("description") or status.get("name") or "",
            }
        )
    return out


def _scoreboard_count(day: date) -> int:
    url = f"{_ESPN_SCOREBOARD}?dates={day.strftime('%Y%m%d')}"
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return 0
    return len(_parse_events(payload, day=day))


def detect_first_match_date(*, today: date | None = None) -> date | None:
    """
    Opening-night-ish slate: first day with >=10 real NBA games
    (filters preseason noise which usually tops out lower).
    """
    now = today or date.today()
    season_year = now.year if now.month >= 8 else now.year - 1
    key = f"{season_year}:v2"
    t = time.time()
    if (
        _FIRST_MATCH_CACHE.get("key") == key
        and _FIRST_MATCH_CACHE.get("date") is not None
        and t - float(_FIRST_MATCH_CACHE.get("at") or 0) < _FIRST_MATCH_TTL_SEC
    ):
        return _FIRST_MATCH_CACHE["date"]

    start = date(season_year, 10, 1)
    found: date | None = None
    for i in range(45):
        day = start + timedelta(days=i)
        if _scoreboard_count(day) >= 10:
            found = day
            break

    _FIRST_MATCH_CACHE["key"] = key
    _FIRST_MATCH_CACHE["at"] = t
    _FIRST_MATCH_CACHE["date"] = found
    return found


def resolve_lockout_window(
    *,
    today: date | None = None,
    days: int = 7,
    week: int | None = None,
) -> dict[str, Any]:
    """Matchup-week window (Mon–Sun, longer All-Star week)."""
    from fantasy_weeks import resolve_matchup_week

    resolved = resolve_matchup_week(week, today=today)
    # `days` kept for API compat; matchup length wins.
    _ = days
    return {
        "today": resolved["today"],
        "first_match": resolved.get("first_match"),
        "window_start": resolved["window_start"],
        "window_end": resolved["window_end"],
        "dates": list(resolved["dates"]),
        "mode": "matchup",
        "week": resolved["week"],
        "week_current": resolved["week_current"],
        "week_max": resolved["week_max"],
        "week_label": resolved["week_label"],
        "days": resolved["days"],
        "is_all_star": resolved.get("is_all_star"),
        "is_playoff": resolved.get("is_playoff"),
        "can_prev": resolved["can_prev"],
        "can_next": resolved["can_next"],
    }


def fetch_scoreboard_days(
    days: int = 7,
    *,
    today: date | None = None,
    start: date | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Return games for a matchup-week window. Cached briefly."""
    now = today or date.today()
    if start is None:
        window = resolve_lockout_window(today=now, days=days, week=week)
        base = window["window_start"]
        date_list: list[date] = list(window["dates"])
        meta = window
    else:
        base = start
        date_list = [base + timedelta(days=i) for i in range(max(1, int(days)))]
        meta = {
            "today": now,
            "first_match": detect_first_match_date(today=now),
            "window_start": base,
            "window_end": date_list[-1],
            "dates": date_list,
            "mode": "custom",
            "week": None,
            "week_current": None,
            "week_max": None,
            "week_label": "Custom",
            "days": len(date_list),
            "is_all_star": False,
            "can_prev": False,
            "can_next": False,
        }

    key = f"{base.isoformat()}:{len(date_list)}:{now.isoformat()}"
    tnow = time.time()
    if (
        _CACHE.get("key") == key
        and _CACHE.get("data") is not None
        and tnow - float(_CACHE.get("at") or 0) < _CACHE_TTL_SEC
    ):
        return dict(_CACHE["data"])

    games: list[dict[str, Any]] = []
    errors: list[str] = []
    for day in date_list:
        url = f"{_ESPN_SCOREBOARD}?dates={day.strftime('%Y%m%d')}"
        try:
            payload = _http_json(url)
            games.extend(_parse_events(payload, day=day))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            errors.append(f"{day.isoformat()}: {exc}")

    by_team: dict[str, list[dict[str, Any]]] = {}
    tonight_teams: set[str] = set()
    today_iso = now.isoformat()
    for g in games:
        for side, opp_side in (("home", "away"), ("away", "home")):
            team = g.get(side)
            opp = g.get(opp_side)
            if not team:
                continue
            entry = {
                "date": g["date"],
                "opponent": opp,
                "is_home": side == "home",
                "start": g.get("start"),
                "status": g.get("status"),
                "state": g.get("state"),
                "is_today": g["date"] == today_iso,
            }
            by_team.setdefault(team, []).append(entry)
            if entry["is_today"]:
                tonight_teams.add(team)

    data = {
        "ok": not errors or bool(games),
        "today": today_iso,
        "days": len(date_list),
        "window_start": meta["window_start"].isoformat(),
        "window_end": meta["window_end"].isoformat(),
        "first_match": meta["first_match"].isoformat() if meta.get("first_match") else None,
        "mode": meta.get("mode"),
        "week": meta.get("week"),
        "week_current": meta.get("week_current"),
        "week_max": meta.get("week_max"),
        "week_label": meta.get("week_label"),
        "is_all_star": bool(meta.get("is_all_star")),
        "can_prev": bool(meta.get("can_prev")),
        "can_next": bool(meta.get("can_next")),
        "date_list": [d.isoformat() for d in date_list],
        "games": games,
        "by_team": by_team,
        "tonight_teams": sorted(tonight_teams),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
    }
    _CACHE["key"] = key
    _CACHE["at"] = tnow
    _CACHE["data"] = data
    return dict(data)
