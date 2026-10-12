"""Full sync: attach Chrome, scrape settings + league, save JSON."""

from __future__ import annotations

from typing import Any

import config
from app_settings import resolve_my_team_id
from chrome_browser import ChromeNotOpenError, attach_chrome
from jobs import JobCancelled, bind_job_driver, raise_if_cancelled, restore_page_load_timeout
from scrapers.league import scrape_league
from scrapers.matchup import scrape_matchups
from scrapers.settings import scrape_settings
from store import load_json, save_json, utc_now_iso


def build_my_team_payload(
    league: dict[str, Any],
    settings: dict[str, Any],
    *,
    my_team_id: str | None = None,
) -> dict[str, Any]:
    slots = []
    for slot in settings.get("roster_positions") or []:
        slots.append(
            {
                **slot,
                "player": None,
            }
        )
    teams = league.get("teams") or []
    mine = next((t for t in teams if str(t.get("team_id")) == str(my_team_id)), None)
    if mine is None:
        mine = league.get("my_team") or {}
    return {
        "team_id": mine.get("team_id") or my_team_id,
        "name": mine.get("name"),
        "manager": mine.get("manager"),
        "logo": mine.get("logo"),
        "url": mine.get("url")
        or (f"{config.LEAGUE_URL}/{my_team_id}" if my_team_id else None),
        "slots": slots,
        "filled": 0,
        "capacity": len(slots),
        "note": "Roster slots from league settings. Players appear after the draft.",
    }


def _team_from_standings(standings: dict[str, Any], team_id: str | None) -> dict[str, Any]:
    if not team_id:
        return {}
    for row in standings.get("rows") or []:
        if str(row.get("team_id")) == str(team_id):
            return row
    return {}


def _matchup_involves(row: dict[str, Any], team_id: str | None) -> bool:
    if not team_id:
        return False
    tid = str(team_id)
    return tid in {
        str((row.get("home") or {}).get("team_id") or ""),
        str((row.get("away") or {}).get("team_id") or ""),
    }


def apply_my_team_selection(*, persist: bool = True) -> dict[str, Any]:
    """Re-label local dashboard JSON for Settings → My team (no scrape)."""
    rules = load_json(config.RULES_PATH, {}) or {}
    standings = load_json(config.STANDINGS_PATH, {}) or {}
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    meta = load_json(config.META_PATH, {}) or {}

    detected = meta.get("yahoo_detected_team_id")
    # Fallback: previously saved mine / any standings row marked mine.
    if not detected:
        detected = meta.get("my_team_id") or my_team.get("team_id")
        if not detected:
            for r in standings.get("rows") or []:
                if r.get("is_mine"):
                    detected = r.get("team_id")
                    break
    resolved = resolve_my_team_id(str(detected) if detected else None)
    team_id = str(resolved) if resolved else None

    row = _team_from_standings(standings, team_id)
    for r in standings.get("rows") or []:
        r["is_mine"] = bool(team_id) and str(r.get("team_id")) == str(team_id)

    # Rebuild My Team card from standings + existing roster slots.
    slots = my_team.get("slots")
    if not slots:
        slots = []
        for slot in rules.get("roster_positions") or []:
            slots.append({**slot, "player": None})
    # Keep players only if same team; otherwise clear filled roster display.
    same_team = str(my_team.get("team_id") or "") == str(team_id or "")
    if not same_team:
        slots = [{**s, "player": None} for s in slots]
    filled = sum(1 for s in slots if s.get("player")) if same_team else 0

    new_my_team = {
        **my_team,
        "team_id": team_id or row.get("team_id"),
        "name": row.get("name") or my_team.get("name"),
        "manager": row.get("manager") or my_team.get("manager"),
        "logo": row.get("logo") or my_team.get("logo"),
        "url": row.get("url")
        or (f"{config.LEAGUE_URL}/{team_id}" if team_id else my_team.get("url")),
        "slots": slots,
        "filled": filled,
        "capacity": len(slots) or my_team.get("capacity"),
    }
    if not same_team:
        new_my_team["note"] = (
            "Team switched in Settings. Run Refresh for this team's "
            "roster details and full season matchups."
        )

    # League matchups: mark mine for selected team.
    rows = [dict(m) for m in (matchups.get("matchups") or [])]
    mine_row = None
    for m in rows:
        m["is_mine"] = _matchup_involves(m, team_id)
        if m["is_mine"] and mine_row is None:
            mine_row = m

    league_week = matchups.get("week")
    schedule_team = matchups.get("my_schedule_team_id")
    schedule = list(matchups.get("my_schedule") or [])
    if schedule and str(schedule_team or "") != str(team_id or ""):
        # Entire schedule belongs to another fantasy team.
        schedule = []
    else:
        # Drop leftover weeks that do not involve the selected team
        # (e.g. week 1 still showing the previous "mine" pairing).
        schedule = [s for s in schedule if _matchup_involves(s, team_id)]

    my_matchup = matchups.get("my_matchup") or {}
    if mine_row:
        home = dict(mine_row.get("home") or {})
        away = dict(mine_row.get("away") or {})
        score = mine_row.get("score") or {}
        old_home = str((my_matchup.get("home") or {}).get("team_id") or "")
        old_away = str((my_matchup.get("away") or {}).get("team_id") or "")
        pair = {str(home.get("team_id") or ""), str(away.get("team_id") or "")}
        keep_cats = {old_home, old_away} == pair and bool(my_matchup.get("categories"))
        if score.get("home") is not None:
            home["categories_won"] = score.get("home")
        if score.get("away") is not None:
            away["categories_won"] = score.get("away")
        my_matchup = {
            "week": mine_row.get("week") or league_week,
            "url": mine_row.get("url") or my_matchup.get("url"),
            "home": home,
            "away": away,
            "categories": my_matchup.get("categories") if keep_cats else [],
            "score": {
                "home": home.get("categories_won"),
                "away": away.get("categories_won"),
            },
            "is_mine": True,
        }
    elif not _matchup_involves(my_matchup, team_id):
        my_matchup = {
            "week": league_week,
            "home": {},
            "away": {},
            "categories": [],
            "is_mine": True,
        }

    # Prefer / inject current-week league matchup into schedule.
    if my_matchup.get("home") or my_matchup.get("away"):
        cur_week = my_matchup.get("week") or league_week
        current_entry = {
            "week": cur_week,
            "url": my_matchup.get("url"),
            "home": my_matchup.get("home") or {},
            "away": my_matchup.get("away") or {},
            "categories": my_matchup.get("categories") or [],
            "score": my_matchup.get("score")
            or {
                "home": (my_matchup.get("home") or {}).get("categories_won"),
                "away": (my_matchup.get("away") or {}).get("categories_won"),
            },
            "is_mine": True,
            "is_current": True,
        }
        replaced = False
        for i, s in enumerate(schedule):
            if cur_week is not None and int(s.get("week") or -1) == int(cur_week):
                # Keep richer categories from schedule if same pairing.
                old_pair = {
                    str((s.get("home") or {}).get("team_id") or ""),
                    str((s.get("away") or {}).get("team_id") or ""),
                }
                new_pair = {
                    str((current_entry["home"] or {}).get("team_id") or ""),
                    str((current_entry["away"] or {}).get("team_id") or ""),
                }
                if old_pair == new_pair and s.get("categories") and not current_entry["categories"]:
                    current_entry["categories"] = s.get("categories") or []
                schedule[i] = current_entry
                replaced = True
                break
        if not replaced:
            schedule.append(current_entry)

    for s in schedule:
        s["is_mine"] = True
        if league_week is not None and s.get("week") is not None:
            try:
                s["is_current"] = int(s["week"]) == int(league_week)
            except (TypeError, ValueError):
                s["is_current"] = False
        else:
            s["is_current"] = bool(s.get("is_current"))

    schedule.sort(key=lambda r: int(r.get("week") or 0))

    new_matchups = {
        **matchups,
        "matchups": rows,
        "my_matchup": my_matchup,
        "my_schedule": schedule,
        "my_schedule_team_id": team_id if schedule else None,
    }

    new_meta = {
        **meta,
        "my_team_id": new_my_team.get("team_id"),
        "my_team_name": new_my_team.get("name"),
    }

    if persist:
        save_json(config.STANDINGS_PATH, standings)
        save_json(config.MY_TEAM_PATH, new_my_team)
        save_json(config.MATCHUPS_PATH, new_matchups)
        save_json(config.META_PATH, new_meta)

    from roster_view import enrich_my_team_db_ids

    return {
        "meta": new_meta,
        "rules": rules,
        "standings": standings,
        "my_team": enrich_my_team_db_ids(new_my_team),
        "matchups": new_matchups,
    }


def sync_all() -> dict[str, Any]:
    driver = attach_chrome()
    restore_page_load_timeout(driver)
    bind_job_driver(driver)
    try:
        raise_if_cancelled()
        settings = scrape_settings(driver)
        raise_if_cancelled()
        league = scrape_league(driver)
        detected_id = (league.get("my_team") or {}).get("team_id")
        if detected_id is not None:
            detected_id = str(detected_id)
        my_team_id = resolve_my_team_id(detected_id)

        # Mark standings/teams is_mine from settings override.
        for row in (league.get("standings") or {}).get("rows") or []:
            row["is_mine"] = str(row.get("team_id")) == str(my_team_id)
        for t in league.get("teams") or []:
            t["is_mine"] = str(t.get("team_id")) == str(my_team_id)

        raise_if_cancelled()
        matchups_raw = scrape_matchups(
            driver,
            teams=league.get("teams") or [],
            my_team_id=my_team_id,
        )
    except JobCancelled:
        raise

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
        **build_my_team_payload(league, settings, my_team_id=my_team_id),
    }
    matchups = {
        "synced_at": synced_at,
        **matchups_raw,
        "my_schedule_team_id": my_team_id,
    }
    meta = {
        "synced_at": synced_at,
        "league_id": config.LEAGUE_ID,
        "league_name": settings.get("league_name") or "",
        "draft_time": (settings.get("settings") or {}).get("Draft Time"),
        "scoring_type": (settings.get("settings") or {}).get("Scoring Type"),
        "yahoo_detected_team_id": detected_id,
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
