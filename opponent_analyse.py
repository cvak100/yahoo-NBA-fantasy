"""Deep opponent analysis: history, injuries, keys, vs recent form, weekly targets."""

from __future__ import annotations

from typing import Any

import config
from categories import CATEGORIES, compare_winner, format_stat, mean, parse_stat
from db import connect, get_player_by_yahoo_id, list_players
from nba_schedule import fetch_scoreboard_days
from nba_teams import resolve_team
from opponent_scouting import (
    _enrich_roster_rows,
    _my_roster_enriched,
    _team_cat_avgs,
    build_scouting,
    current_opponent_id,
    list_fantasy_teams,
    load_cached_opponent,
)
from store import load_json

_STAT_TO_KEY = {
    "FG%": "fg_pct",
    "FT%": "ft_pct",
    "3PTM": "threes",
    "PTS": "pts",
    "REB": "reb",
    "AST": "ast",
    "ST": "stl",
    "STL": "stl",
    "BLK": "blk",
    "TO": "tov",
    "TOV": "tov",
}


def _my_id() -> str:
    my = load_json(config.MY_TEAM_PATH, {}) or {}
    return str(my.get("team_id") or "").strip()


def _team_abbr(raw: str | None) -> str | None:
    info = resolve_team(raw)
    if info:
        return str(info["abbr"])
    s = str(raw or "").strip().upper()
    return s or None


def _involves(entry: dict[str, Any], team_id: str) -> str | None:
    """Return 'home' / 'away' if team is in entry."""
    tid = str(team_id)
    if str((entry.get("home") or {}).get("team_id") or "") == tid:
        return "home"
    if str((entry.get("away") or {}).get("team_id") or "") == tid:
        return "away"
    return None


def _opp_matchup_history(opp_id: str, *, last_n: int = 6) -> list[dict[str, Any]]:
    """Past league matchups for opponent (score + categories when we played them)."""
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    my_id = _my_id()
    rows: list[dict[str, Any]] = []

    # Detailed cats when I faced them (from my_schedule).
    detail_by_week: dict[int, dict[str, Any]] = {}
    for entry in matchups.get("my_schedule") or []:
        side = _involves(entry, opp_id)
        if not side:
            continue
        week = entry.get("week")
        if week is None:
            continue
        cats = []
        for raw in entry.get("categories") or []:
            stat = str(raw.get("stat") or "").strip()
            key = _STAT_TO_KEY.get(stat)
            if not key:
                continue
            # Values from my_schedule are home/away of that matchup page.
            their_val = raw.get(side)
            my_side = "away" if side == "home" else "home"
            my_val = raw.get(my_side) if my_id and _involves(entry, my_id) else None
            cats.append(
                {
                    "key": key,
                    "stat": stat,
                    "theirs": their_val,
                    "mine": my_val,
                    "winner": raw.get("winner"),
                }
            )
        detail_by_week[int(week)] = {
            "week": int(week),
            "vs_me": True,
            "categories": cats,
            "score": entry.get("score"),
            "home": entry.get("home"),
            "away": entry.get("away"),
        }

    # All league rows involving opponent.
    for entry in matchups.get("matchups") or []:
        side = _involves(entry, opp_id)
        if not side:
            continue
        week = entry.get("week")
        if week is None:
            continue
        week_i = int(week)
        opp = entry.get(side) or {}
        other_side = "away" if side == "home" else "home"
        foe = entry.get(other_side) or {}
        score = entry.get("score") or {}
        their_cats = score.get(side)
        foe_cats = score.get(other_side)
        detail = detail_by_week.get(week_i)
        rows.append(
            {
                "week": week_i,
                "opponent_name": foe.get("name"),
                "opponent_id": str(foe.get("team_id") or "") or None,
                "their_cats_won": their_cats,
                "foe_cats_won": foe_cats,
                "result": (
                    "W"
                    if their_cats is not None
                    and foe_cats is not None
                    and their_cats > foe_cats
                    else "L"
                    if their_cats is not None
                    and foe_cats is not None
                    and their_cats < foe_cats
                    else "T"
                    if their_cats is not None
                    and foe_cats is not None
                    and their_cats == foe_cats
                    else None
                ),
                "vs_me": bool(detail and detail.get("vs_me")),
                "categories": (detail or {}).get("categories") or [],
                "url": entry.get("url"),
            }
        )

    # Prefer unique weeks (detail wins).
    by_week: dict[int, dict[str, Any]] = {}
    for r in rows:
        w = r["week"]
        prev = by_week.get(w)
        if prev is None or (r.get("categories") and not prev.get("categories")):
            by_week[w] = r
    ordered = [by_week[k] for k in sorted(by_week.keys())]
    return ordered[-max(1, int(last_n)) :]


def _avg_cats_from_history(history: list[dict[str, Any]], *, last_n: int = 3) -> dict[str, Any]:
    """Average opponent category values from last N matchups that have cat lines."""
    recent = [h for h in history if h.get("categories")][-last_n:]
    if not recent:
        return {"ok": False, "weeks_used": 0, "averages": {}, "note": "No per-category history vs this team yet."}
    sums: dict[str, list[float]] = {c["key"]: [] for c in CATEGORIES}
    for h in recent:
        for cat in h.get("categories") or []:
            key = cat.get("key")
            v = parse_stat(cat.get("theirs"))
            if key in sums and v is not None:
                sums[key].append(v)
    averages = []
    for c in CATEGORIES:
        vals = sums[c["key"]]
        avg = mean(vals)
        averages.append(
            {
                "key": c["key"],
                "label": c["label"],
                "kind": c["kind"],
                "lower_better": c["lower_better"],
                "value": avg,
                "display": format_stat(avg, kind=c["kind"]),
                "samples": len(vals),
            }
        )
    return {
        "ok": True,
        "weeks_used": len(recent),
        "week_numbers": [h["week"] for h in recent],
        "averages": averages,
    }


def _injuries_for_roster(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    yahoo_ids = set()
    names = set()
    nba_teams = set()
    for s in slots:
        p = s.get("player")
        if not p:
            continue
        yid = str(p.get("yahoo_id") or "").strip()
        if yid:
            yahoo_ids.add(yid)
        if p.get("name"):
            names.add(str(p["name"]).strip().lower())
        ab = _team_abbr(p.get("nba_team"))
        if ab:
            nba_teams.add(ab)

    conn = connect()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT i.*, p.yahoo_id, p.full_name, p.yahoo_rank, p.nba_team
                FROM injuries i
                LEFT JOIN players p ON p.id = i.player_id
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    out = []
    for r in rows:
        yid = str(r.get("yahoo_id") or "").strip()
        pname = str(r.get("full_name") or r.get("player_name") or "").strip().lower()
        hit = (yid and yid in yahoo_ids) or (pname and pname in names)
        if not hit:
            continue
        out.append(
            {
                "name": r.get("full_name") or r.get("player_name"),
                "yahoo_id": yid or None,
                "db_id": r.get("player_id"),
                "nba_team": r.get("nba_team") or r.get("team_name"),
                "injury": r.get("injury"),
                "injury_status": r.get("injury_status"),
                "yahoo_rank": r.get("yahoo_rank"),
                "updated_at": r.get("updated_at"),
            }
        )
    out.sort(key=lambda x: (x.get("yahoo_rank") is None, x.get("yahoo_rank") or 9999))
    return out


def _key_players(slots: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    players = []
    for s in slots:
        p = s.get("player")
        if not p:
            continue
        try:
            rank = int(p["yahoo_rank"]) if p.get("yahoo_rank") is not None else None
        except (TypeError, ValueError):
            rank = None
        # Simple impact: counting stats sum
        volume = 0.0
        n = 0
        for key in ("pts", "reb", "ast", "stl", "blk", "threes"):
            v = parse_stat(p.get(key))
            if v is not None:
                volume += v
                n += 1
        players.append(
            {
                "yahoo_id": p.get("yahoo_id"),
                "db_id": p.get("db_id"),
                "name": p.get("name"),
                "nba_team": p.get("nba_team"),
                "positions": p.get("positions"),
                "slot": s.get("slot_position"),
                "yahoo_rank": rank,
                "volume": round(volume, 1) if n else None,
                "why": (
                    f"rank #{rank}" if rank and rank <= 50 else
                    f"volume {volume:.0f}" if n else
                    "rostered"
                ),
            }
        )
    players.sort(
        key=lambda r: (
            r.get("yahoo_rank") is None,
            r.get("yahoo_rank") or 9999,
            -(r.get("volume") or 0),
        )
    )
    return players[:limit]


def _games_left_roster(slots: list[dict[str, Any]], schedule: dict[str, Any]) -> dict[str, Any]:
    by_team = schedule.get("by_team") or {}
    today = str(schedule.get("today") or "")
    total = 0
    per_player = []
    for s in slots:
        p = s.get("player")
        if not p:
            continue
        ab = _team_abbr(p.get("nba_team"))
        games = by_team.get(ab or "") or []
        left = [
            g
            for g in games
            if str(g.get("date") or "") >= today and str(g.get("state") or "").lower() != "post"
        ]
        n = len(left)
        total += n
        per_player.append({"name": p.get("name"), "games_left": n, "nba_team": p.get("nba_team")})
    return {"team_games_left": total, "players": per_player}


def build_weekly_targets(opp_id: str | None = None) -> dict[str, Any]:
    """What you still need this week vs opponent (live matchup + pace)."""
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    mine = matchups.get("my_matchup") or {}
    my_id = _my_id()
    opp_id = str(opp_id or current_opponent_id() or "").strip()

    my_side = _involves(mine, my_id) if my_id else None
    opp_side = _involves(mine, opp_id) if opp_id else None
    if not my_side or not opp_side:
        return {
            "ok": False,
            "note": "No current matchup loaded — run Dashboard Refresh.",
            "targets": [],
        }

    schedule = fetch_scoreboard_days()
    scout = build_scouting(opp_id)
    my_slots = scout.get("my_team", {}).get("slots") or []
    opp_slots = scout.get("opponent", {}).get("slots") or []
    my_games = _games_left_roster(my_slots, schedule)
    opp_games = _games_left_roster(opp_slots, schedule)

    targets = []
    for raw in mine.get("categories") or []:
        stat = str(raw.get("stat") or "").strip()
        key = _STAT_TO_KEY.get(stat)
        cat = next((c for c in CATEGORIES if c["key"] == key), None)
        if not cat:
            continue
        my_val = parse_stat(raw.get(my_side))
        their_val = parse_stat(raw.get(opp_side))
        lower = bool(cat["lower_better"])
        winner = compare_winner(my_val, their_val, lower_better=lower)

        # Gap to take the lead (or pad a lead).
        need = None
        need_display = None
        if my_val is not None and their_val is not None:
            if lower:
                # Need lower than them: target = their_val - epsilon, need_delta = my - target
                target = their_val - (0.1 if cat["kind"] != "pct" else 0.001)
                need = my_val - target  # positive => must reduce by this
            else:
                target = their_val + (0.1 if cat["kind"] != "pct" else 0.001)
                need = target - my_val  # positive => must gain this
            need_display = format_stat(abs(need), kind=cat["kind"]) if need is not None else None

        status = "winning" if winner == "mine" else "losing" if winner == "theirs" else "tied" if winner == "tie" else "unknown"
        advice = None
        if need is not None:
            if status == "winning" and need <= 0:
                advice = "Hold the lead"
            elif status == "losing" or (need is not None and need > 0):
                if lower:
                    advice = f"Cut ~{need_display} more than them (or stream cleaner TO)"
                else:
                    advice = f"Need ~+{need_display} to pass them"
            else:
                advice = "Edge is thin — one stream can flip it"

        targets.append(
            {
                "key": key,
                "label": cat["label"],
                "kind": cat["kind"],
                "lower_better": lower,
                "mine": my_val,
                "mine_display": format_stat(my_val, kind=cat["kind"]),
                "theirs": their_val,
                "theirs_display": format_stat(their_val, kind=cat["kind"]),
                "status": status,
                "need": need,
                "need_display": need_display,
                "advice": advice,
                "my_games_left": my_games["team_games_left"],
                "their_games_left": opp_games["team_games_left"],
            }
        )

    at_risk = [t for t in targets if t["status"] in {"losing", "tied"}]
    return {
        "ok": True,
        "week": mine.get("week") or matchups.get("week"),
        "week_label": schedule.get("week_label"),
        "my_games_left": my_games["team_games_left"],
        "their_games_left": opp_games["team_games_left"],
        "targets": targets,
        "at_risk": [t["label"] for t in at_risk],
        "note": "Live Yahoo matchup totals vs what you still need to flip/hold each cat.",
    }


def build_form_comparison(opp_id: str, *, last_n: int = 3) -> dict[str, Any]:
    """My season roster avgs vs opponent's last N matchup cat averages (or roster)."""
    scout = build_scouting(opp_id)
    my_avgs = _team_cat_avgs(scout.get("my_team", {}).get("slots") or [])
    history = _opp_matchup_history(opp_id, last_n=max(6, last_n))
    form = _avg_cats_from_history(history, last_n=last_n)
    opp_roster_avgs = _team_cat_avgs(scout.get("opponent", {}).get("slots") or [])

    rows = []
    for c in CATEGORIES:
        mine = my_avgs.get(c["key"])
        form_row = next((a for a in form.get("averages") or [] if a["key"] == c["key"]), None)
        theirs = (form_row or {}).get("value")
        source = "recent_matchups" if theirs is not None else "roster_season"
        if theirs is None:
            theirs = opp_roster_avgs.get(c["key"])
        winner = compare_winner(mine, theirs, lower_better=bool(c["lower_better"]))
        rows.append(
            {
                "key": c["key"],
                "label": c["label"],
                "kind": c["kind"],
                "lower_better": c["lower_better"],
                "mine": mine,
                "mine_display": format_stat(mine, kind=c["kind"]),
                "theirs": theirs,
                "theirs_display": format_stat(theirs, kind=c["kind"]),
                "winner": winner,
                "source": source,
                "risk": winner == "theirs",
            }
        )
    risks = [r["label"] for r in rows if r.get("risk")]
    return {
        "ok": True,
        "last_n": last_n,
        "form": form,
        "rows": rows,
        "risk_cats": risks,
        "note": (
            f"You vs their last {form.get('weeks_used') or 0} scored matchups "
            "(fallback: season roster averages)."
        ),
    }


def build_opponent_analyse(team_id: str | None = None, *, last_n: int = 3) -> dict[str, Any]:
    scout = build_scouting(team_id)
    opp_id = str(scout.get("selected_team_id") or "")
    history = _opp_matchup_history(opp_id, last_n=8) if opp_id else []
    form = build_form_comparison(opp_id, last_n=last_n) if opp_id else {"ok": False, "rows": []}
    targets = build_weekly_targets(opp_id) if opp_id else {"ok": False, "targets": []}
    slots = (scout.get("opponent") or {}).get("slots") or []
    injuries = _injuries_for_roster(slots)
    keys = _key_players(slots)

    return {
        "ok": True,
        **scout,
        "history": history,
        "history_note": "Cats-won from league board; full category lines when you played them.",
        "form": form,
        "weekly_targets": targets,
        "injuries": injuries,
        "key_players": keys,
        "last_n": last_n,
    }
