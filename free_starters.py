"""Available NBA starters from CBS depth charts who are FA/W in fantasy."""

from __future__ import annotations

from typing import Any

from db import connect, find_player_id_by_name
from decisions import _is_available_owner
from scrapers.cbs_depth import load_cbs_depth
from scrapers.player_db import primary_position
from store import utc_now_iso

STARTER_SLOTS = ("PG", "SG", "SF", "PF", "C")


def _lookup_players_by_id() -> dict[int, dict[str, Any]]:
    """Lightweight map id → player row with season avg (same shape as list_players)."""
    from db import list_players

    return {int(p["id"]): p for p in list_players() if p.get("id") is not None}


def load_available_starters(*, refresh_cbs: bool = False) -> dict[str, Any]:
    """
    CBS Sports depth-chart starters (PG–C × team) who are FA / waivers.

    Yahoo Forecast / local AR ranking is NOT used — only CBS Starter column.
    """
    if refresh_cbs:
        from scrapers.cbs_depth import sync_cbs_depth

        sync_cbs_depth()

    cbs = load_cbs_depth()
    starters_raw = list(cbs.get("starters") or [])
    if not starters_raw:
        return {
            "ok": True,
            "scraped_at": cbs.get("scraped_at"),
            "source": "cbsports",
            "source_url": cbs.get("source_url"),
            "note": (
                "No CBS depth chart cached yet. Click Refresh starters "
                "(https://www.cbssports.com/fantasy/basketball/depth-chart/)."
            ),
            "count": 0,
            "starter_slots_total": 0,
            "starter_slots_taken": 0,
            "starter_slots_available": 0,
            "players": [],
            "unmatched": [],
        }

    by_id = _lookup_players_by_id()
    conn = connect()
    try:
        rows: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []
        seen: set[str] = set()

        for s in starters_raw:
            team = str(s.get("nba_team") or "").strip().upper()
            slot = str(s.get("position") or "").strip().upper()
            name = str(s.get("name") or "").strip()
            if slot not in STARTER_SLOTS or not name:
                continue

            pid = find_player_id_by_name(name, conn=conn)
            player = by_id.get(int(pid)) if pid else None
            if not player:
                unmatched.append({"nba_team": team, "starter_slot": slot, "name": name})
                continue

            yid = str(player.get("yahoo_id") or "").strip()
            key = yid or f"{team}:{slot}:{name}"
            if key in seen:
                continue
            seen.add(key)

            row = {
                "yahoo_id": yid,
                "db_id": player.get("id"),
                "name": player.get("full_name") or name,
                "cbs_name": name,
                "nba_team": team or player.get("nba_team"),
                "positions": player.get("positions"),
                "primary_pos": primary_position(player.get("positions")),
                "starter_slot": slot,
                "yahoo_rank": player.get("yahoo_rank"),
                "owned_pct": player.get("owned_pct"),
                "fantasy_owner": player.get("fantasy_owner"),
                "fg_pct": player.get("fg_pct"),
                "ft_pct": player.get("ft_pct"),
                "threes": player.get("threes"),
                "pts": player.get("pts"),
                "reb": player.get("reb"),
                "ast": player.get("ast"),
                "stl": player.get("stl"),
                "blk": player.get("blk"),
                "tov": player.get("tov"),
                "source": "cbs_depth",
            }
            rows.append(row)
    finally:
        conn.close()

    available = [r for r in rows if _is_available_owner(r.get("fantasy_owner"))]
    available.sort(
        key=lambda r: (
            r.get("yahoo_rank") is None,
            r.get("yahoo_rank") or 10_000,
            r.get("nba_team") or "",
            STARTER_SLOTS.index(r["starter_slot"])
            if r.get("starter_slot") in STARTER_SLOTS
            else 99,
        )
    )

    return {
        "ok": True,
        "scraped_at": cbs.get("scraped_at") or utc_now_iso(),
        "source": "cbsports",
        "source_url": cbs.get("source_url")
        or "https://www.cbssports.com/fantasy/basketball/depth-chart/",
        "note": (
            "Starters = CBS Sports depth-chart Starter column (PG/SG/SF/PF/C). "
            "Showing only those still FA / waivers in your league."
        ),
        "count": len(available),
        "starter_slots_total": len(rows),
        "starter_slots_taken": len(rows) - len(available),
        "starter_slots_available": len(available),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched[:20],
        "players": available,
    }
