"""Resolve which fantasy team owns a player (You / opponent / FA)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import config
from store import load_json

_FREE_STATUSES = frozenset({"FA", "W", "WA", "FREE", "FREE AGENT", "—"})


def _norm(s: str | None) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _is_free(status: str | None) -> bool:
    raw = str(status or "").strip()
    if not raw:
        return False
    if raw in {"FA", "W", "WA"} or raw.startswith("W ("):
        return True
    return _norm(raw) in { _norm(x) for x in _FREE_STATUSES }


def _standings_teams() -> list[dict[str, Any]]:
    standings = load_json(config.STANDINGS_PATH, {}) or {}
    return list(standings.get("rows") or [])


def _match_team_label(raw: str, teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = _norm(raw)
    if not key:
        return None
    # Numeric Yahoo team id ("3") → standings row
    if re.fullmatch(r"\d+", key):
        for t in teams:
            if str(t.get("team_id") or "").strip() == key:
                return t
        return None
    for t in teams:
        name = str(t.get("name") or "")
        if _norm(name) == key:
            return t
        if key in _norm(name) or _norm(name) in key:
            return t
        # Yahoo sometimes shows abbreviated manager/team crumbs
        mgr = str(t.get("manager") or "")
        if mgr and _norm(mgr) == key:
            return t
    return None


def _looks_like_stat_crumb(raw: str) -> bool:
    """True for MPG/REB/GP/% crumbs accidentally scraped as Owner."""
    s = str(raw or "").strip()
    if not s:
        return True
    if re.fullmatch(r"\d+", s):
        return True
    if re.fullmatch(r"\d+\.\d+", s):
        return True
    if re.fullmatch(r"\d+(\.\d+)?%", s):
        return True
    if re.fullmatch(r"\d+:\d+", s):  # MPG like 34:51
        return True
    if re.fullmatch(r"\d+(\.\d+)?/\d+(\.\d+)?", s):  # FGM/A
        return True
    if re.fullmatch(r"\.\d+", s):  # .569 FG%
        return True
    return False


def _resolve_owner_label(raw: str | None, teams: list[dict[str, Any]] | None = None) -> str | None:
    """Map Yahoo status crumb / team id → display team name (or FA/W)."""
    status = str(raw or "").strip()
    if not status:
        return None
    if _is_free(status):
        return "FA" if not status.startswith("W") else ("Waivers" if status == "W" else status)
    team_list = teams if teams is not None else _standings_teams()
    # Numeric Yahoo team id ("9") → standings name (before stat-crumb reject).
    if re.fullmatch(r"\d+", status):
        match = _match_team_label(status, team_list)
        if match:
            return str(match.get("name") or "").strip() or None
        return None
    if _looks_like_stat_crumb(status):
        return None
    match = _match_team_label(status, team_list)
    if match:
        return str(match.get("name") or status).strip() or status
    return status


def _set_owner(
    out: dict[str, dict[str, Any]],
    yahoo_id: str,
    *,
    label: str,
    is_mine: bool,
    team_id: str | None = None,
    source: str,
) -> None:
    pid = str(yahoo_id or "").strip()
    if not pid:
        return
    prev = out.get(pid)
    # Prefer explicit roster scrapes over generic Yahoo status cells.
    rank = {"roster": 3, "opponent": 3, "status": 1}.get(source, 0)
    prev_rank = {"roster": 3, "opponent": 3, "status": 1}.get((prev or {}).get("source") or "", 0)
    if prev and prev_rank > rank:
        return
    out[pid] = {
        "yahoo_id": pid,
        "label": label,
        "is_mine": bool(is_mine),
        "is_taken": not _is_free(label) and bool(label),
        "team_id": str(team_id) if team_id else None,
        "source": source,
    }
    if _is_free(label):
        out[pid]["is_taken"] = False
        out[pid]["label"] = "FA" if not str(label).startswith("W") else "Waivers"


def ownership_by_yahoo_id() -> dict[str, dict[str, Any]]:
    """Map yahoo_id → ownership info for UI."""
    out: dict[str, dict[str, Any]] = {}
    teams = _standings_teams()
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_id = str(my_team.get("team_id") or "")
    my_name = str(my_team.get("name") or "You")

    for slot in my_team.get("slots") or []:
        p = slot.get("player") or {}
        yahoo_id = str(p.get("yahoo_id") or "").strip()
        if yahoo_id:
            _set_owner(
                out,
                yahoo_id,
                label="You",
                is_mine=True,
                team_id=my_id or None,
                source="roster",
            )

    roster_dir = Path(config.DATA_DIR) / "opponent_rosters"
    if roster_dir.is_dir():
        for path in roster_dir.glob("*.json"):
            cached = load_json(path, {}) or {}
            tid = str(cached.get("team_id") or path.stem)
            if my_id and tid == my_id:
                continue
            tname = str(cached.get("name") or f"Team {tid}")
            for row in cached.get("raw") or []:
                if row.get("empty"):
                    continue
                yahoo_id = str(row.get("yahoo_id") or row.get("player_id") or "").strip()
                if yahoo_id:
                    _set_owner(
                        out,
                        yahoo_id,
                        label=tname,
                        is_mine=False,
                        team_id=tid,
                        source="opponent",
                    )

    # Fallback: Yahoo status stored on players rows (FA / W / team crumb).
    try:
        from db import connect

        conn = connect()
        try:
            rows = conn.execute(
                "SELECT yahoo_id, fantasy_owner FROM players WHERE fantasy_owner IS NOT NULL AND fantasy_owner != ''"
            ).fetchall()
        finally:
            conn.close()
        for r in rows:
            yahoo_id = str(r["yahoo_id"] or "").strip()
            status = str(r["fantasy_owner"] or "").strip()
            if not yahoo_id or not status:
                continue
            resolved = _resolve_owner_label(status, teams)
            if not resolved:
                continue
            if _is_free(resolved):
                _set_owner(out, yahoo_id, label=resolved, is_mine=False, source="status")
                continue
            # Never invent "You" from a stale DB cell — my_team roster is source of truth.
            if _norm(resolved) in {_norm("You"), _norm(my_name)}:
                continue
            match = _match_team_label(resolved, teams) or _match_team_label(status, teams)
            if match and str(match.get("team_id") or "") == my_id:
                continue
            if match:
                _set_owner(
                    out,
                    yahoo_id,
                    label=str(match.get("name") or resolved),
                    is_mine=False,
                    team_id=str(match.get("team_id") or "") or None,
                    source="status",
                )
            else:
                _set_owner(out, yahoo_id, label=resolved, is_mine=False, source="status")
    except Exception:
        pass

    return out


def _reassert_my_roster_owners() -> int:
    from db import clear_stale_you_owners, set_fantasy_owners

    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    mine: dict[str, str] = {}
    for slot in my_team.get("slots") or []:
        p = slot.get("player") or {}
        yid = str(p.get("yahoo_id") or "").strip()
        if yid:
            mine[yid] = "You"
    if mine:
        set_fantasy_owners(mine)
        clear_stale_you_owners(set(mine.keys()))
    else:
        clear_stale_you_owners(set())
    return len(mine)


def lookup_owner_for_player(
    driver,
    *,
    yahoo_id: str,
    name: str | None = None,
) -> str | None:
    """Look up one player's Yahoo Status (FA / W / team) via player search."""
    from scrapers.players import search_players

    yid = str(yahoo_id or "").strip()
    if not yid:
        return None
    query = (name or "").strip() or yid
    # Prefer last name for tighter search hits.
    parts = query.split()
    if len(parts) >= 2:
        query = parts[-1]
    try:
        data = search_players(driver, query, limit=25)
    except Exception:
        return None
    for p in data.get("players") or []:
        if str(p.get("player_id") or "").strip() == yid:
            status = str(p.get("status") or "").strip()
            return status or None
    return None


def update_owner_for_player(
    driver,
    *,
    yahoo_id: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Fetch + persist fantasy_owner for one player. Re-asserts You from my roster."""
    from db import set_fantasy_owners

    yid = str(yahoo_id or "").strip()
    status = lookup_owner_for_player(driver, yahoo_id=yid, name=name)
    label = _resolve_owner_label(status)
    if label:
        set_fantasy_owners({yid: label})
    _reassert_my_roster_owners()
    return {"ok": True, "yahoo_id": yid, "fantasy_owner": label}


def sync_ownership_from_yahoo_list(
    driver,
    *,
    limit: int = 900,
    progress_cb=None,
) -> dict[str, Any]:
    """Scrape Yahoo players?status=ALL: Owner + Rankings→Current + %Ros."""
    from scrapers.players import ranked_players
    from db import connect, set_fantasy_owners, set_yahoo_current_ranks

    if progress_cb:
        progress_cb(0, 1, "Loading Yahoo ownership + Current ranks (ALL)…")
    # sort=AR = Yahoo "Current" column (Pre-Season is sort=OR).
    raws = ranked_players(driver, limit=max(50, int(limit)), status="ALL", sort="AR")
    teams = _standings_teams()
    owners: dict[str, str] = {}
    ranks: dict[str, int | None] = {}
    owned_pct: dict[str, str] = {}
    for r in raws:
        yid = str(r.get("player_id") or r.get("yahoo_id") or "").strip()
        if not yid:
            continue
        status = str(r.get("status") or "").strip()
        label = _resolve_owner_label(status, teams) if status else None
        if label:
            owners[yid] = label
        # Current only — blank clears stale Pre-Season values in DB.
        rank_raw = str(r.get("rank") or "").strip()
        if rank_raw.isdigit():
            ranks[yid] = int(rank_raw)
        else:
            ranks[yid] = None
        pct = str(r.get("owned_pct") or "").strip()
        if pct:
            owned_pct[yid] = pct
    updated = set_fantasy_owners(owners) if owners else 0
    # Drop every stored rank first so Pre-Season leftovers cannot coexist
    # with Current (e.g. Wemby still at 2 next to SGA Current=2).
    if ranks:
        conn = connect()
        try:
            conn.execute("UPDATE players SET yahoo_rank = NULL")
            conn.commit()
        finally:
            conn.close()
    ranks_updated = set_yahoo_current_ranks(ranks, owned=owned_pct) if ranks or owned_pct else 0
    ranked_n = sum(1 for v in ranks.values() if v is not None)
    cleared_n = sum(1 for v in ranks.values() if v is None)
    mine = _reassert_my_roster_owners()
    if progress_cb:
        progress_cb(
            1,
            1,
            f"Owners {len(owners)} · Current ranks {ranked_n} (cleared {cleared_n})",
        )
    return {
        "ok": True,
        "scraped": len(raws),
        "owners": len(owners),
        "updated": updated,
        "ranks": ranked_n,
        "ranks_cleared": cleared_n,
        "ranks_updated": ranks_updated,
        "mine": mine,
    }


def enrich_players_with_ownership(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Drop stale You marks for players no longer on my roster (no Chrome needed).
    try:
        from db import clear_stale_you_owners

        my_team = load_json(config.MY_TEAM_PATH, {}) or {}
        keep = set()
        for slot in my_team.get("slots") or []:
            yid = str((slot.get("player") or {}).get("yahoo_id") or "").strip()
            if yid:
                keep.add(yid)
        clear_stale_you_owners(keep)
    except Exception:
        pass

    own = ownership_by_yahoo_id()
    enriched = []
    for p in players:
        row = dict(p)
        yahoo_id = str(row.get("yahoo_id") or "").strip()
        info = own.get(yahoo_id)
        if info:
            row["fantasy_team"] = info.get("label")
            row["fantasy_is_mine"] = bool(info.get("is_mine"))
            row["fantasy_is_taken"] = bool(info.get("is_taken"))
            row["fantasy_team_id"] = info.get("team_id")
        else:
            # Unknown — treat as free for hide-taken unless owned_pct says otherwise.
            row["fantasy_team"] = None
            row["fantasy_is_mine"] = False
            row["fantasy_is_taken"] = False
            row["fantasy_team_id"] = None
        enriched.append(row)
    return enriched
