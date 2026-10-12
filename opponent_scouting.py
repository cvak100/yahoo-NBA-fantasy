"""Opponent scouting: fantasy roster cache + category matchup vs my team."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config
from categories import (
    CATEGORIES,
    compare_winner,
    format_stat,
    mean,
    parse_stat,
)
from db import get_player_by_yahoo_id, list_players
from store import load_json, save_json, utc_now_iso


def _rosters_dir() -> Path:
    path = config.DATA_DIR / "opponent_rosters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _roster_path(team_id: str) -> Path:
    return _rosters_dir() / f"{team_id}.json"


def list_fantasy_teams() -> list[dict[str, Any]]:
    standings = load_json(config.STANDINGS_PATH, {}) or {}
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_id = str(my_team.get("team_id") or "")
    rows = []
    for r in standings.get("rows") or []:
        tid = str(r.get("team_id") or "")
        if not tid:
            continue
        rows.append(
            {
                "team_id": tid,
                "name": r.get("name") or f"Team {tid}",
                "manager": r.get("manager") or "",
                "logo": r.get("logo") or "",
                "record": r.get("record") or "",
                "rank": r.get("rank"),
                "is_mine": bool(r.get("is_mine")) or tid == my_id,
                "url": r.get("url") or f"{config.LEAGUE_URL}/{tid}",
            }
        )
    rows.sort(key=lambda t: (0 if t["is_mine"] else 1, int(t.get("rank") or 99), t["name"]))
    return rows


def current_opponent_id() -> str | None:
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_id = str(my_team.get("team_id") or "")
    mm = matchups.get("my_matchup") or {}
    home = str((mm.get("home") or {}).get("team_id") or "")
    away = str((mm.get("away") or {}).get("team_id") or "")
    if my_id and home and away:
        if home == my_id:
            return away or None
        if away == my_id:
            return home or None
    for m in matchups.get("matchups") or []:
        if not m.get("is_mine"):
            continue
        h = str((m.get("home") or {}).get("team_id") or "")
        a = str((m.get("away") or {}).get("team_id") or "")
        if h == my_id:
            return a or None
        if a == my_id:
            return h or None
    return None


def _enrich_roster_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_yahoo = {str(p.get("yahoo_id")): p for p in list_players() if p.get("yahoo_id")}
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        if row.get("empty") or not (row.get("name") or row.get("yahoo_id") or row.get("player_id")):
            out.append(
                {
                    "empty": True,
                    "slot_position": row.get("slot_position") or "",
                    "player": None,
                }
            )
            continue
        yahoo_id = str(row.get("yahoo_id") or row.get("player_id") or "").strip()
        db = by_yahoo.get(yahoo_id) if yahoo_id else None
        if not db and yahoo_id:
            db = get_player_by_yahoo_id(yahoo_id)
        player = {
            "yahoo_id": yahoo_id,
            "db_id": (db or {}).get("id"),
            "name": (db or {}).get("full_name") or row.get("name"),
            "nba_team": (db or {}).get("nba_team") or row.get("nba_team"),
            "positions": (db or {}).get("positions") or row.get("positions"),
            "yahoo_rank": (db or {}).get("yahoo_rank"),
            "gp": (db or {}).get("gp"),
        }
        for cat in CATEGORIES:
            player[cat["field"]] = (db or {}).get(cat["field"])
        out.append(
            {
                "empty": False,
                "slot_position": row.get("slot_position") or "",
                "player": player,
            }
        )
    return out


def _my_roster_enriched() -> list[dict[str, Any]]:
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    raw = []
    for slot in my_team.get("slots") or []:
        p = slot.get("player")
        if not p:
            raw.append({"empty": True, "slot_position": slot.get("position") or ""})
            continue
        raw.append(
            {
                "empty": False,
                "slot_position": slot.get("position") or "",
                "yahoo_id": p.get("yahoo_id"),
                "player_id": p.get("yahoo_id"),
                "name": p.get("name"),
                "nba_team": p.get("team"),
                "positions": p.get("positions"),
            }
        )
    return _enrich_roster_rows(raw)


def _team_cat_avgs(slots: list[dict[str, Any]]) -> dict[str, float | None]:
    avgs: dict[str, float | None] = {}
    for cat in CATEGORIES:
        vals = []
        for s in slots:
            p = s.get("player")
            if not p:
                continue
            v = parse_stat(p.get(cat["field"]))
            if v is not None:
                vals.append(v)
        avgs[cat["key"]] = mean(vals)
    return avgs


def load_cached_opponent(team_id: str) -> dict[str, Any] | None:
    path = _roster_path(str(team_id))
    if not path.exists():
        return None
    return load_json(path, None)


def save_opponent_roster(team_id: str, raw_rows: list[dict[str, Any]], *, meta: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "team_id": str(team_id),
        "name": meta.get("name"),
        "manager": meta.get("manager"),
        "logo": meta.get("logo"),
        "synced_at": utc_now_iso(),
        "raw": raw_rows,
    }
    save_json(_roster_path(str(team_id)), payload)
    try:
        from db import set_fantasy_owners

        label = str(meta.get("name") or f"Team {team_id}").strip()
        owners = {}
        for row in raw_rows or []:
            if row.get("empty"):
                continue
            yid = str(row.get("yahoo_id") or row.get("player_id") or "").strip()
            if yid:
                owners[yid] = label
        if owners:
            set_fantasy_owners(owners)
    except Exception:
        pass
    return payload


def sync_opponent_roster(team_id: str, *, progress_cb=None) -> dict[str, Any]:
    from chrome_browser import attach_chrome
    from scrapers.main_refresh import scrape_my_roster_players

    tid = str(team_id)
    teams = {t["team_id"]: t for t in list_fantasy_teams()}
    meta = teams.get(tid) or {"team_id": tid, "name": f"Team {tid}"}
    if progress_cb:
        progress_cb(0, 2, f"Opening Yahoo roster for {meta.get('name')}…")
    driver = attach_chrome()
    raw = scrape_my_roster_players(driver, tid)
    filled = sum(1 for r in raw if not r.get("empty"))
    if progress_cb:
        progress_cb(1, 2, f"Saved {filled} players.")
    cached = save_opponent_roster(tid, raw, meta=meta)
    if progress_cb:
        progress_cb(2, 2, "Done.")
    return {"ok": True, "team_id": tid, "count": filled, "synced_at": cached.get("synced_at")}


def build_scouting(team_id: str | None = None) -> dict[str, Any]:
    teams = list_fantasy_teams()
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_id = str(my_team.get("team_id") or "")
    opp_id = str(team_id or current_opponent_id() or "")
    if opp_id and opp_id == my_id:
        # Pick first non-mine if user passed own team.
        other = next((t for t in teams if not t["is_mine"]), None)
        opp_id = str((other or {}).get("team_id") or "")

    opp_meta = next((t for t in teams if t["team_id"] == opp_id), None) or {
        "team_id": opp_id,
        "name": f"Team {opp_id}" if opp_id else "—",
    }

    cached = load_cached_opponent(opp_id) if opp_id else None
    opp_slots = _enrich_roster_rows((cached or {}).get("raw") or []) if cached else []
    my_slots = _my_roster_enriched()

    my_avgs = _team_cat_avgs(my_slots)
    opp_avgs = _team_cat_avgs(opp_slots)

    comparisons = []
    edges = []
    for cat in CATEGORIES:
        mine = my_avgs.get(cat["key"])
        theirs = opp_avgs.get(cat["key"])
        winner = compare_winner(mine, theirs, lower_better=bool(cat["lower_better"]))
        row = {
            "key": cat["key"],
            "label": cat["label"],
            "kind": cat["kind"],
            "lower_better": cat["lower_better"],
            "mine": mine,
            "mine_display": format_stat(mine, kind=cat["kind"]),
            "theirs": theirs,
            "theirs_display": format_stat(theirs, kind=cat["kind"]),
            "winner": winner,
        }
        comparisons.append(row)
        if winner == "mine":
            edges.append(cat["label"])

    return {
        "ok": True,
        "teams": [t for t in teams if not t["is_mine"]],
        "all_teams": teams,
        "current_opponent_id": current_opponent_id(),
        "selected_team_id": opp_id or None,
        "opponent": {
            **opp_meta,
            "synced_at": (cached or {}).get("synced_at"),
            "has_roster": bool(cached),
            "filled": sum(1 for s in opp_slots if s.get("player")),
            "slots": opp_slots,
        },
        "my_team": {
            "team_id": my_id,
            "name": my_team.get("name"),
            "logo": my_team.get("logo"),
            "filled": sum(1 for s in my_slots if s.get("player")),
            "slots": my_slots,
        },
        "comparisons": comparisons,
        "edges": edges,
        "edge_count": len(edges),
    }
