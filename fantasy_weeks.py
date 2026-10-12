"""Yahoo-style fantasy matchup weeks (Mon–Sun, longer All-Star week)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

import config
from nba_schedule import detect_first_match_date
from store import load_json

_ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
_AS_CACHE: dict[str, Any] = {"key": None, "at": 0.0, "date": None}
_AS_TTL_SEC = 12 * 3600.0
_WEEKS_CACHE: dict[str, Any] = {"key": None, "weeks": None}


def _monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _http_json(url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "yahoonba/1.0 (fantasy weeks)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_all_star_date(*, season_year: int | None = None, today: date | None = None) -> date | None:
    """
    Find All-Star Saturday (usually mid-Feb empty slate).
    season_year = autumn year of the season (2026 for 2026-27).
    """
    now = today or date.today()
    if season_year is None:
        season_year = now.year if now.month >= 8 else now.year - 1
    key = str(season_year)
    t = time.time()
    if (
        _AS_CACHE.get("key") == key
        and _AS_CACHE.get("date") is not None
        and t - float(_AS_CACHE.get("at") or 0) < _AS_TTL_SEC
    ):
        return _AS_CACHE["date"]

    # All-Star is in the calendar year after season tip-off.
    feb = date(season_year + 1, 2, 10)
    found: date | None = None
    for i in range(20):
        d = feb + timedelta(days=i)
        if d.weekday() != 5:  # Saturday
            continue
        url = f"{_ESPN_SCOREBOARD}?dates={d.strftime('%Y%m%d')}"
        try:
            payload = _http_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        events = payload.get("events") or []
        names = " ".join(
            f"{e.get('name') or ''} {e.get('shortName') or ''}" for e in events
        ).lower()
        if "all-star" in names or "all star" in names:
            found = d
            break
        # Empty Saturday in AS window ≈ All-Star break.
        if not events:
            found = d
            break

    _AS_CACHE["key"] = key
    _AS_CACHE["at"] = t
    _AS_CACHE["date"] = found
    return found


def league_week_count() -> int:
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    weeks = {
        int(s["week"])
        for s in (matchups.get("my_schedule") or [])
        if s.get("week") is not None
    }
    if weeks:
        return max(weeks)
    raw = matchups.get("week")
    try:
        return max(21, int(raw or 21))
    except (TypeError, ValueError):
        return 21


def league_current_week() -> int | None:
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    try:
        w = int(matchups.get("week"))
        return w if w > 0 else None
    except (TypeError, ValueError):
        return None


def matchup_week1_start(*, today: date | None = None) -> date:
    """
    Matchup Week 1 starts Monday of the week that contains opening night.
    Falls back to next/current Monday if opening night unknown.
    """
    now = today or date.today()
    first = detect_first_match_date(today=now)
    if first:
        return _monday_on_or_before(first)
    return _monday_on_or_before(now)


def build_matchup_weeks(
    *,
    today: date | None = None,
    max_week: int | None = None,
) -> list[dict[str, Any]]:
    """
    Build matchup weeks: normal = 7 days (Mon–Sun).
    The week that contains All-Star Saturday is 14 days (Yahoo combines two weeks).
    """
    now = today or date.today()
    season_year = now.year if now.month >= 8 else now.year - 1
    n_weeks = max(1, int(max_week or league_week_count()))
    week1 = matchup_week1_start(today=now)
    as_date = detect_all_star_date(season_year=season_year, today=now)
    cache_key = f"{week1.isoformat()}:{as_date}:{n_weeks}"
    if _WEEKS_CACHE.get("key") == cache_key and _WEEKS_CACHE.get("weeks"):
        return list(_WEEKS_CACHE["weeks"])

    # Last 3 weeks = playoffs (from league settings); All-Star long week
    # must land in the regular season before that.
    playoff_first = max(1, n_weeks - 2)
    regular_last = playoff_first - 1

    weeks: list[dict[str, Any]] = []
    cursor = week1
    as_assigned = False
    for w in range(1, n_weeks + 1):
        end7 = cursor + timedelta(days=6)
        is_as = False
        if w <= regular_last and as_date and not as_assigned:
            if cursor <= as_date <= end7:
                is_as = True
            elif w == regular_last and as_date >= cursor:
                # AS falls on what would be a playoff calendar week — fold into last regular matchup.
                is_as = True
        length = 14 if is_as else 7
        if is_as:
            as_assigned = True
        end = cursor + timedelta(days=length - 1)
        weeks.append(
            {
                "week": w,
                "start": cursor,
                "end": end,
                "days": length,
                "is_all_star": is_as,
                "is_playoff": w >= playoff_first,
                "label": (
                    f"Matchup W{w}"
                    + (" · All-Star" if is_as else "")
                    + (" · Playoffs" if w >= playoff_first else "")
                ),
            }
        )
        cursor = end + timedelta(days=1)

    _WEEKS_CACHE["key"] = cache_key
    _WEEKS_CACHE["weeks"] = weeks
    return list(weeks)


def resolve_matchup_week(
    week: int | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Pick a matchup week window for lockouts UI."""
    now = today or date.today()
    weeks = build_matchup_weeks(today=now)
    if not weeks:
        mon = _monday_on_or_before(now)
        return {
            "week": 1,
            "week_current": 1,
            "week_max": 1,
            "window_start": mon,
            "window_end": mon + timedelta(days=6),
            "dates": [mon + timedelta(days=i) for i in range(7)],
            "days": 7,
            "is_all_star": False,
            "week_label": "Matchup W1",
            "can_prev": False,
            "can_next": False,
            "first_match": detect_first_match_date(today=now),
            "today": now,
        }

    # Current = league week if set, else week that contains today (or week 1 if before).
    league_w = league_current_week()
    cur = None
    for row in weeks:
        if row["start"] <= now <= row["end"]:
            cur = int(row["week"])
            break
    if cur is None:
        if now < weeks[0]["start"]:
            cur = 1
        else:
            cur = int(weeks[-1]["week"])
    if league_w and 1 <= league_w <= int(weeks[-1]["week"]):
        # Prefer Yahoo league week when we're still preseason / early.
        if now < weeks[0]["start"] or now <= weeks[0]["end"]:
            cur = league_w

    week_max = int(weeks[-1]["week"])
    if week is None:
        week_n = cur
    else:
        try:
            week_n = int(week)
        except (TypeError, ValueError):
            week_n = cur
    week_n = max(1, min(week_max, week_n))
    row = next(r for r in weeks if int(r["week"]) == week_n)
    dates = [row["start"] + timedelta(days=i) for i in range(int(row["days"]))]
    is_current = week_n == cur
    label = str(row["label"])
    if is_current:
        label += " · now"

    return {
        "week": week_n,
        "week_current": cur,
        "week_max": week_max,
        "window_start": row["start"],
        "window_end": row["end"],
        "dates": dates,
        "days": int(row["days"]),
        "is_all_star": bool(row["is_all_star"]),
        "is_playoff": bool(row.get("is_playoff")),
        "week_label": label,
        "can_prev": week_n > 1,
        "can_next": week_n < week_max,
        "first_match": detect_first_match_date(today=now),
        "today": now,
        "weeks": weeks,
    }
