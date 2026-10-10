"""Full sync: attach Chrome, scrape settings + league, save JSON."""

from __future__ import annotations

from typing import Any

import config
from chrome_browser import ChromeNotOpenError, attach_chrome
from scrapers.league import scrape_league
from scrapers.matchup import scrape_matchups
from scrapers.settings import scrape_settings
from store import save_json, utc_now_iso


def build_my_team_payload(
    league: dict[str, Any], settings: dict[str, Any]
) -> dict[str, Any]:
    slots = []
    for slot in settings.get("roster_positions") or []:
        slots.append(
            {
                **slot,
                "player": None,
            }
        )
    mine = league.get("my_team") or {}
    return {
        "team_id": mine.get("team_id"),
        "name": mine.get("name"),
        "manager": mine.get("manager"),
        "logo": mine.get("logo"),
        "url": mine.get("url"),
        "slots": slots,
        "filled": 0,
        "capacity": len(slots),
        "note": "Roster slots from league settings. Players appear after the draft.",
    }


def sync_all() -> dict[str, Any]:
    driver = attach_chrome()
    settings = scrape_settings(driver)
    league = scrape_league(driver)
    matchups_raw = scrape_matchups(
        driver,
        teams=league.get("teams") or [],
        my_team_id=(league.get("my_team") or {}).get("team_id"),
    )

    synced_at = utc_now_iso()
    rules = {
        "synced_at": synced_at,
        **settings,
    }
    standings = {
        "synced_at": synced_at,
        "league_id": config.LEAGUE_ID,
        "league_name": settings.get("league_name") or "",
        **league["standings"],
        "teams_count": len(league.get("teams") or []),
    }
    my_team = {
        "synced_at": synced_at,
        **build_my_team_payload(league, settings),
    }
    matchups = {
        "synced_at": synced_at,
        **matchups_raw,
    }
    meta = {
        "synced_at": synced_at,
        "league_id": config.LEAGUE_ID,
        "league_name": settings.get("league_name") or "",
        "draft_time": (settings.get("settings") or {}).get("Draft Time"),
        "scoring_type": (settings.get("settings") or {}).get("Scoring Type"),
        "my_team_name": my_team.get("name"),
        "my_team_id": my_team.get("team_id"),
        "week": matchups.get("week"),
    }

    save_json(config.RULES_PATH, rules)
    save_json(config.STANDINGS_PATH, standings)
    save_json(config.MY_TEAM_PATH, my_team)
    save_json(config.MATCHUPS_PATH, matchups)
    save_json(config.META_PATH, meta)

    return {
        "ok": True,
        "meta": meta,
        "rules": rules,
        "standings": standings,
        "my_team": my_team,
        "matchups": matchups,
    }


def sync_or_raise() -> dict[str, Any]:
    try:
        return sync_all()
    except ChromeNotOpenError:
        raise
