"""NBA team abbreviations, display names, and logo URLs."""

from __future__ import annotations

import re
from typing import Any

_TEAMS: list[dict[str, Any]] = [
    {"abbr": "ATL", "city": "Atlanta", "name": "Atlanta Hawks", "aliases": ["hawks"]},
    {"abbr": "BOS", "city": "Boston", "name": "Boston Celtics", "aliases": ["celtics"]},
    {"abbr": "BKN", "city": "Brooklyn", "name": "Brooklyn Nets", "aliases": ["brk", "nets"]},
    {"abbr": "CHA", "city": "Charlotte", "name": "Charlotte Hornets", "aliases": ["cho", "hornets"]},
    {"abbr": "CHI", "city": "Chicago", "name": "Chicago Bulls", "aliases": ["bulls"]},
    {"abbr": "CLE", "city": "Cleveland", "name": "Cleveland Cavaliers", "aliases": ["cavs", "cavaliers"]},
    {"abbr": "DAL", "city": "Dallas", "name": "Dallas Mavericks", "aliases": ["mavs", "mavericks"]},
    {"abbr": "DEN", "city": "Denver", "name": "Denver Nuggets", "aliases": ["nuggets"]},
    {"abbr": "DET", "city": "Detroit", "name": "Detroit Pistons", "aliases": ["pistons"]},
    {
        "abbr": "GSW",
        "city": "Golden St.",
        "name": "Golden State Warriors",
        "aliases": ["gs", "warriors", "golden state", "golden st"],
    },
    {"abbr": "HOU", "city": "Houston", "name": "Houston Rockets", "aliases": ["rockets"]},
    {"abbr": "IND", "city": "Indiana", "name": "Indiana Pacers", "aliases": ["pacers"]},
    {
        "abbr": "LAC",
        "city": "L.A. Clippers",
        "name": "LA Clippers",
        "aliases": ["clippers", "la clippers", "l.a. clippers", "los angeles clippers"],
    },
    {
        "abbr": "LAL",
        "city": "L.A. Lakers",
        "name": "Los Angeles Lakers",
        "aliases": ["lakers", "la lakers", "l.a. lakers"],
    },
    {"abbr": "MEM", "city": "Memphis", "name": "Memphis Grizzlies", "aliases": ["grizzlies"]},
    {"abbr": "MIA", "city": "Miami", "name": "Miami Heat", "aliases": ["heat"]},
    {"abbr": "MIL", "city": "Milwaukee", "name": "Milwaukee Bucks", "aliases": ["bucks"]},
    {
        "abbr": "MIN",
        "city": "Minnesota",
        "name": "Minnesota Timberwolves",
        "aliases": ["wolves", "timberwolves"],
    },
    {
        "abbr": "NOP",
        "city": "New Orleans",
        "name": "New Orleans Pelicans",
        "aliases": ["no", "pelicans"],
    },
    {"abbr": "NYK", "city": "New York", "name": "New York Knicks", "aliases": ["ny", "knicks"]},
    {
        "abbr": "OKC",
        "city": "Oklahoma City",
        "name": "Oklahoma City Thunder",
        "aliases": ["thunder"],
    },
    {"abbr": "ORL", "city": "Orlando", "name": "Orlando Magic", "aliases": ["magic"]},
    {
        "abbr": "PHI",
        "city": "Philadelphia",
        "name": "Philadelphia 76ers",
        "aliases": ["sixers", "76ers"],
    },
    {"abbr": "PHX", "city": "Phoenix", "name": "Phoenix Suns", "aliases": ["pho", "suns"]},
    {
        "abbr": "POR",
        "city": "Portland",
        "name": "Portland Trail Blazers",
        "aliases": ["blazers", "trail blazers"],
    },
    {"abbr": "SAC", "city": "Sacramento", "name": "Sacramento Kings", "aliases": ["kings"]},
    {
        "abbr": "SAS",
        "city": "San Antonio",
        "name": "San Antonio Spurs",
        "aliases": ["sa", "spurs"],
    },
    {"abbr": "TOR", "city": "Toronto", "name": "Toronto Raptors", "aliases": ["raptors"]},
    {"abbr": "UTA", "city": "Utah", "name": "Utah Jazz", "aliases": ["jazz"]},
    {
        "abbr": "WAS",
        "city": "Washington",
        "name": "Washington Wizards",
        "aliases": ["wsh", "wizards"],
    },
]


def normalize_team_key(value: str | None) -> str:
    raw = str(value or "").lower().replace(".", "")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def logo_url(abbr: str | None) -> str:
    code = str(abbr or "").strip().lower()
    if not code:
        return ""
    return f"https://a.espncdn.com/i/teamlogos/nba/500/{code}.png"


_BY_KEY: dict[str, dict[str, Any]] = {}
for _t in _TEAMS:
    entry = {
        **_t,
        "logo": logo_url(_t["abbr"]),
    }
    keys = {
        normalize_team_key(_t["abbr"]),
        normalize_team_key(_t["city"]),
        normalize_team_key(_t["name"]),
    }
    for alias in _t.get("aliases") or []:
        keys.add(normalize_team_key(alias))
    for key in keys:
        if key:
            _BY_KEY[key] = entry


def resolve_team(team: str | None) -> dict[str, Any] | None:
    key = normalize_team_key(team)
    if not key or key in {"—", "-", "team", "unknown"}:
        return None
    return _BY_KEY.get(key)


def team_keys_for(team: str | None) -> set[str]:
    info = resolve_team(team)
    if not info:
        key = normalize_team_key(team)
        return {key} if key else set()
    keys = {
        normalize_team_key(info["abbr"]),
        normalize_team_key(info["city"]),
        normalize_team_key(info["name"]),
    }
    for alias in info.get("aliases") or []:
        keys.add(normalize_team_key(alias))
    return {k for k in keys if k}


def enrich_team_group(team: str | None) -> dict[str, Any]:
    info = resolve_team(team)
    if not info:
        return {"team": team or "—", "abbr": None, "logo": None, "display_name": team or "—"}
    return {
        "team": team or info["abbr"],
        "abbr": info["abbr"],
        "logo": info["logo"],
        "display_name": info["name"],
        "city": info["city"],
    }
