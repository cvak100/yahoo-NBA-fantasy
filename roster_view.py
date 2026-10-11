"""Build My Roster page payload: slots + DB stats + news for my players."""

from __future__ import annotations

from typing import Any

import config
from db import connect, get_player_by_yahoo_id, list_players, normalize_player_name
from store import load_json


def enrich_my_team_db_ids(my_team: dict[str, Any] | None) -> dict[str, Any]:
    """Attach local DB player ids onto my_team.slots[*].player for detail modals."""
    team = dict(my_team or {})
    slots = []
    for slot in team.get("slots") or []:
        s = dict(slot)
        player = s.get("player")
        if player:
            p = dict(player)
            yahoo_id = str(p.get("yahoo_id") or "").strip()
            if yahoo_id and not p.get("db_id"):
                row = get_player_by_yahoo_id(yahoo_id)
                if row:
                    p["db_id"] = row["id"]
            s["player"] = p
        slots.append(s)
    team["slots"] = slots
    return team


def _news_for_player_ids(player_ids: set[int], *, limit: int = 300) -> list[dict[str, Any]]:
    if not player_ids:
        return []
    conn = connect()
    try:
        placeholders = ",".join("?" for _ in player_ids)
        ids = list(player_ids)
        fantasy = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    f.published_at,
                    f.published_iso,
                    f.source,
                    f.title,
                    f.body,
                    f.player_id,
                    f.player_name,
                    f.team_name,
                    p.full_name AS matched_name,
                    p.nba_team,
                    p.positions,
                    p.yahoo_rank
                FROM fantasy_news f
                JOIN players p ON p.id = f.player_id
                WHERE f.player_id IN ({placeholders})
                ORDER BY
                    CASE WHEN f.published_iso IS NULL OR f.published_iso = '' THEN 1 ELSE 0 END,
                    f.published_iso DESC,
                    f.id DESC
                """,
                ids,
            ).fetchall()
        ]
        yahoo = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT
                    s.published_at,
                    NULL AS published_iso,
                    s.source,
                    s.title,
                    s.body,
                    s.player_id,
                    p.full_name AS player_name,
                    p.nba_team AS team_name,
                    p.full_name AS matched_name,
                    p.nba_team,
                    p.positions,
                    p.yahoo_rank
                FROM player_stats s
                JOIN players p ON p.id = s.player_id
                WHERE s.record_type = 'news' AND s.player_id IN ({placeholders})
                ORDER BY s.id DESC
                """,
                ids,
            ).fetchall()
        ]
        by_title: dict[str, dict[str, Any]] = {}
        untitled: list[dict[str, Any]] = []
        for row in fantasy + yahoo:
            title_key = normalize_player_name(row.get("title") or "")
            if not title_key:
                untitled.append(row)
                continue
            prev = by_title.get(title_key)
            if prev is None or len(row.get("body") or "") > len(prev.get("body") or ""):
                by_title[title_key] = row
        merged = list(by_title.values()) + untitled
        with_iso = [n for n in merged if n.get("published_iso")]
        without_iso = [n for n in merged if not n.get("published_iso")]
        with_iso.sort(key=lambda n: str(n.get("published_iso") or ""), reverse=True)
        return (with_iso + without_iso)[: max(1, min(int(limit), 500))]
    finally:
        conn.close()


def load_my_roster_page() -> dict[str, Any]:
    my_team = enrich_my_team_db_ids(load_json(config.MY_TEAM_PATH, {}) or {})
    slots = list(my_team.get("slots") or [])
    by_yahoo = {str(p.get("yahoo_id")): p for p in list_players() if p.get("yahoo_id")}

    detail_slots: list[dict[str, Any]] = []
    db_ids: set[int] = set()
    for slot in slots:
        player = slot.get("player") or None
        enriched = None
        if player:
            yahoo_id = str(player.get("yahoo_id") or "").strip()
            db_row = by_yahoo.get(yahoo_id) if yahoo_id else None
            db_id = player.get("db_id") or (db_row or {}).get("id")
            if db_id:
                try:
                    db_ids.add(int(db_id))
                except (TypeError, ValueError):
                    pass
            enriched = {
                **player,
                "db_id": db_id,
                "full_name": (db_row or {}).get("full_name") or player.get("name"),
                "yahoo_rank": (db_row or {}).get("yahoo_rank"),
                "owned_pct": (db_row or {}).get("owned_pct"),
                "gp": (db_row or {}).get("gp"),
                "fg_pct": (db_row or {}).get("fg_pct"),
                "ft_pct": (db_row or {}).get("ft_pct"),
                "threes": (db_row or {}).get("threes"),
                "pts": (db_row or {}).get("pts"),
                "reb": (db_row or {}).get("reb"),
                "ast": (db_row or {}).get("ast"),
                "stl": (db_row or {}).get("stl"),
                "blk": (db_row or {}).get("blk"),
                "tov": (db_row or {}).get("tov"),
                "is_favourite": (db_row or {}).get("is_favourite"),
                "nba_team": (db_row or {}).get("nba_team") or player.get("team"),
                "eligible_positions": (db_row or {}).get("positions")
                or player.get("positions"),
            }
        detail_slots.append(
            {
                "position": slot.get("position"),
                "label": slot.get("label") or slot.get("position"),
                "group": slot.get("group") or "starter",
                "slot_index": slot.get("slot_index"),
                "player": enriched,
            }
        )

    news = _news_for_player_ids(db_ids, limit=300)
    filled = sum(1 for s in detail_slots if s.get("player"))
    return {
        "ok": True,
        "team": {
            "team_id": my_team.get("team_id"),
            "name": my_team.get("name"),
            "manager": my_team.get("manager"),
            "logo": my_team.get("logo"),
            "url": my_team.get("url"),
            "filled": filled,
            "capacity": len(detail_slots),
            "synced_at": my_team.get("synced_at"),
        },
        "slots": detail_slots,
        "news": news,
        "news_count": len(news),
        "player_count": filled,
    }
