"""Resolve which fantasy team owns a player (You / opponent / FA)."""

from __future__ import annotations

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
            if _is_free(status):
                _set_owner(out, yahoo_id, label=status, is_mine=False, source="status")
                continue
            match = _match_team_label(status, teams)
            if match and str(match.get("team_id") or "") == my_id:
                _set_owner(
                    out,
                    yahoo_id,
                    label="You",
                    is_mine=True,
                    team_id=my_id,
                    source="status",
                )
            elif match:
                _set_owner(
                    out,
                    yahoo_id,
                    label=str(match.get("name") or status),
                    is_mine=False,
                    team_id=str(match.get("team_id") or "") or None,
                    source="status",
                )
            else:
                _set_owner(out, yahoo_id, label=status, is_mine=False, source="status")
    except Exception:
        pass

    return out


def enrich_players_with_ownership(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
