"""Build My Roster page payload: slots + coverage + availability + contribution."""

from __future__ import annotations

from typing import Any

import config
from categories import CATEGORIES, format_stat, mean, parse_stat, strength_label
from db import connect, get_player_by_yahoo_id, list_players, normalize_player_name
from nba_schedule import fetch_scoreboard_days
from nba_teams import resolve_team
from store import load_json

COVERAGE_SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "Util")

# Fantasy slot ← player position tokens that can fill it.
_SLOT_NEEDS: dict[str, frozenset[str]] = {
    "PG": frozenset({"PG"}),
    "SG": frozenset({"SG"}),
    "G": frozenset({"PG", "SG", "G"}),
    "SF": frozenset({"SF"}),
    "PF": frozenset({"PF"}),
    "F": frozenset({"SF", "PF", "F"}),
    "C": frozenset({"C"}),
    "Util": frozenset({"PG", "SG", "G", "SF", "PF", "F", "C", "Util"}),
}

_OUT_STATUSES = frozenset(
    {
        "out",
        "o",
        "il",
        "injured reserve",
        "ir",
        "susp",
        "suspended",
        "nwt",
        "na",
        "dnp",
        "inactive",
    }
)


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


def _parse_pos_tokens(raw: str | None) -> set[str]:
    text = str(raw or "").upper().replace("/", ",")
    out: set[str] = set()
    for part in text.replace(" ", ",").split(","):
        tok = part.strip()
        if not tok:
            continue
        if tok in {"UTIL", "UTILITY"}:
            out.add("Util")
        else:
            out.add(tok)
    return out


def player_fills_slot(positions: set[str], slot: str) -> bool:
    if slot == "Util":
        return bool(positions)
    needed = _SLOT_NEEDS.get(slot, frozenset({slot}))
    # Player listed as G can fill PG/SG/G flex rules loosely — Yahoo lists specific elig.
    return bool(positions & needed)


def _group_bucket(group: str | None, position: str | None) -> str:
    g = str(group or "").lower()
    pos = str(position or "").upper()
    if g == "il" or pos in {"IL", "IL+", "IR"}:
        return "il"
    if g == "bench" or pos in {"BN", "BENCH"}:
        return "bench"
    return "active"


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


def _injuries_by_player_id(player_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    conn = connect()
    try:
        placeholders = ",".join("?" for _ in player_ids)
        rows = conn.execute(
            f"""
            SELECT player_id, player_name, injury, injury_status, team_name, updated_at
            FROM injuries
            WHERE player_id IN ({placeholders})
            """,
            list(player_ids),
        ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            pid = int(r["player_id"])
            out[pid] = dict(r)
        return out
    finally:
        conn.close()


def _is_out_status(status: str | None) -> bool:
    raw = str(status or "").strip().lower()
    if not raw:
        return False
    if raw in _OUT_STATUSES:
        return True
    if raw.startswith("out"):
        return True
    return False


def _slot_required_counts(slots: list[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in COVERAGE_SLOTS}
    for s in slots:
        pos = str(s.get("position") or "")
        if pos in counts:
            counts[pos] += 1
    # Fallback from league rules if slots empty of those positions
    if sum(counts.values()) == 0:
        rules = load_json(config.RULES_PATH, {}) or {}
        for s in rules.get("roster_positions") or []:
            pos = str(s.get("position") or "")
            if pos in counts:
                counts[pos] += 1
    return counts


def _build_coverage(players: list[dict[str, Any]], required: dict[str, int]) -> list[dict[str, Any]]:
    rows = []
    for slot in COVERAGE_SLOTS:
        active = bench = il = 0
        names_active: list[str] = []
        for p in players:
            pos_set = _parse_pos_tokens(p.get("eligible_positions") or p.get("positions"))
            if not player_fills_slot(pos_set, slot):
                continue
            bucket = p.get("bucket") or "active"
            name = p.get("full_name") or p.get("name") or "?"
            if bucket == "il":
                il += 1
            elif bucket == "bench":
                bench += 1
            else:
                active += 1
                names_active.append(str(name))
        total = active + bench + il
        need = int(required.get(slot) or 0)
        rows.append(
            {
                "slot": slot,
                "required": need,
                "active": active,
                "bench": bench,
                "il": il,
                "total": total,
                "thin": total < max(need, 1) if need else total < 1,
                "ok": total >= need if need else total > 0,
                "sample": names_active[:3],
            }
        )
    return rows


def _std(values: list[float], avg: float) -> float:
    import math

    if len(values) < 2:
        return 0.0
    var = sum((v - avg) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _build_contribution(
    players: list[dict[str, Any]],
    pool: list[dict[str, Any]],
) -> dict[str, Any]:
    baselines: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        vals = [v for p in pool if (v := parse_stat(p.get(cat["field"]))) is not None]
        avg = mean(vals)
        if avg is None:
            continue
        baselines[cat["key"]] = {"mean": avg, "std": _std(vals, avg)}

    active = [p for p in players if p.get("bucket") == "active"]
    strength = []
    for cat in CATEGORIES:
        field = cat["field"]
        lower = bool(cat["lower_better"])
        kind = cat["kind"]
        my_vals = [v for p in active if (v := parse_stat(p.get(field))) is not None]
        league_vals = [v for p in pool if (v := parse_stat(p.get(field))) is not None]
        my_avg = mean(my_vals)
        league_avg = mean(league_vals)
        label = strength_label(my_avg, league_avg, lower_better=lower)
        strength.append(
            {
                "key": cat["key"],
                "label": cat["label"],
                "strength": label,
                "my_avg_display": format_stat(my_avg, kind=kind),
                "league_avg_display": format_stat(league_avg, kind=kind),
            }
        )

    badges_by_yahoo: dict[str, list[dict[str, Any]]] = {}
    for p in players:
        yahoo_id = str(p.get("yahoo_id") or "")
        scored: list[dict[str, Any]] = []
        for cat in CATEGORIES:
            base = baselines.get(cat["key"])
            if not base:
                continue
            val = parse_stat(p.get(cat["field"]))
            if val is None:
                continue
            std = base["std"]
            z = 0.0 if std <= 1e-9 else (val - base["mean"]) / std
            if cat["lower_better"]:
                z = -z
            scored.append(
                {
                    "key": cat["key"],
                    "label": cat["label"],
                    "z": z,
                    "positive": z >= 0.35,
                    "negative": z <= -0.35,
                }
            )
        scored.sort(key=lambda r: r["z"], reverse=True)
        helps = [x for x in scored if x["positive"]][:2]
        hurts = [x for x in sorted(scored, key=lambda r: r["z"]) if x["negative"]][:1]
        badges: list[dict[str, Any]] = []
        for h in helps:
            badges.append({"key": h["key"], "label": h["label"], "kind": "help"})
        for h in hurts:
            badges.append({"key": h["key"], "label": h["label"], "kind": "hurt"})
        if yahoo_id:
            badges_by_yahoo[yahoo_id] = badges

    return {
        "scope": "active",
        "strength": strength,
        "summary": {
            "strong": sum(1 for s in strength if s["strength"] == "strong"),
            "average": sum(1 for s in strength if s["strength"] == "average"),
            "weak": sum(1 for s in strength if s["strength"] == "weak"),
        },
        "badges_by_yahoo_id": badges_by_yahoo,
    }


def _starter_slot_positions() -> list[str]:
    """Active fantasy slots only (not BN / IL)."""
    rules = load_json(config.RULES_PATH, {}) or {}
    slots = []
    for s in rules.get("roster_positions") or []:
        pos = str(s.get("position") or "")
        group = str(s.get("group") or "").lower()
        if group == "starter" or (
            pos in COVERAGE_SLOTS and group not in {"bench", "il"}
        ):
            if pos.upper() in {"BN", "IL", "IL+", "IR"}:
                continue
            slots.append(pos if pos != "UTIL" else "Util")
    if slots:
        return slots
    return ["PG", "SG", "G", "SF", "PF", "F", "C", "C", "Util", "Util"]


def _max_start_assignment(
    candidates: list[dict[str, Any]],
    starter_slots: list[str],
) -> tuple[set[str], list[dict[str, Any]]]:
    """
    Max-cardinality matching players → starter slots.
    Prefer keeping higher-ranked / active players in the lineup so
    overflow names tend to be the ones left on BN.
    Returns (started_yahoo_ids, overflow_player_dicts).
    """
    if not candidates or not starter_slots:
        return set(), list(candidates)

    ordered = sorted(
        enumerate(candidates),
        key=lambda it: (
            0 if (it[1].get("bucket") or "") == "active" else 1,
            int(it[1].get("yahoo_rank") or 9999),
            str(it[1].get("name") or ""),
        ),
    )
    # Remap to dense indices in preference order
    players = [p for _, p in ordered]
    slot_n = len(starter_slots)
    match: list[int | None] = [None] * slot_n  # slot → player idx

    def eligible_slots(p: dict[str, Any]) -> list[int]:
        pos_set = _parse_pos_tokens(p.get("eligible_positions") or p.get("positions"))
        return [i for i, slot in enumerate(starter_slots) if player_fills_slot(pos_set, slot)]

    edges = [eligible_slots(p) for p in players]

    def dfs(pi: int, seen: set[int]) -> bool:
        for si in edges[pi]:
            if si in seen:
                continue
            seen.add(si)
            if match[si] is None or dfs(match[si], seen):
                match[si] = pi
                return True
        return False

    for pi in range(len(players)):
        dfs(pi, set())

    started_idx = {m for m in match if m is not None}
    started_ids = {str(players[i].get("yahoo_id") or "") for i in started_idx}
    overflow = [players[i] for i in range(len(players)) if i not in started_idx]
    return started_ids, overflow


def _weekday_short(iso: str) -> str:
    try:
        from datetime import date as _date

        return _date.fromisoformat(iso).strftime("%a")
    except ValueError:
        return iso


def _build_availability(
    players: list[dict[str, Any]],
    injuries: dict[int, dict[str, Any]],
    *,
    week: int | None = None,
) -> dict[str, Any]:
    """
    Per-day start/sit congestion: who has a game but cannot fit into
    starter slots (PG…Util) because those slots are already filled.
    """
    schedule = fetch_scoreboard_days(7, week=week)
    by_team = schedule.get("by_team") or {}
    today = schedule.get("today")
    starter_slots = _starter_slot_positions()
    slot_cap = len(starter_slots)
    week_label = schedule.get("week_label") or "Week"

    # Enrich players with team abbr + game dates + out flag
    enriched: list[dict[str, Any]] = []
    for p in players:
        if (p.get("bucket") or "") == "il":
            continue
        team_raw = p.get("nba_team") or p.get("team") or ""
        info = resolve_team(team_raw)
        abbr = (info or {}).get("abbr") or str(team_raw).strip().upper() or None
        db_id = p.get("db_id")
        try:
            db_id_int = int(db_id) if db_id is not None else None
        except (TypeError, ValueError):
            db_id_int = None
        inj = injuries.get(db_id_int) if db_id_int else None
        inj_status = (inj or {}).get("injury_status")
        is_out = _is_out_status(inj_status)
        team_games = list(by_team.get(abbr) or []) if abbr else []
        game_dates = [str(g.get("date")) for g in team_games if g.get("date")]
        tonight = next((g for g in team_games if g.get("is_today")), None)
        enriched.append(
            {
                **p,
                "nba_team": abbr or team_raw,
                "injury_status": inj_status,
                "injury": (inj or {}).get("injury"),
                "is_out": is_out,
                "game_dates": game_dates,
                "week_games": len(game_dates),
                "tonight_display": (
                    f"{'vs' if tonight.get('is_home') else '@'} {tonight.get('opponent')}"
                    if tonight and tonight.get("opponent")
                    else None
                ),
            }
        )

    # Window starts at first NBA match week (not rolling from "today" preseason).
    dates = list(schedule.get("date_list") or [])
    if not dates and today:
        dates = [str(today)]

    days_out: list[dict[str, Any]] = []
    overflow_by_player: dict[str, list[str]] = {}
    total_overflow = 0

    for day in dates:
        candidates = []
        for p in enriched:
            if p.get("is_out"):
                continue
            if day not in (p.get("game_dates") or []):
                continue
            candidates.append(
                {
                    "yahoo_id": p.get("yahoo_id"),
                    "db_id": p.get("db_id"),
                    "name": p.get("full_name") or p.get("name"),
                    "nba_team": p.get("nba_team"),
                    "eligible_positions": p.get("eligible_positions") or p.get("positions"),
                    "positions": p.get("positions"),
                    "bucket": p.get("bucket"),
                    "yahoo_rank": p.get("yahoo_rank"),
                }
            )
        started_ids, overflow = _max_start_assignment(candidates, starter_slots)
        overflow_rows = []
        for o in overflow:
            yid = str(o.get("yahoo_id") or "")
            overflow_rows.append(
                {
                    "yahoo_id": yid,
                    "db_id": o.get("db_id"),
                    "name": o.get("name"),
                    "nba_team": o.get("nba_team"),
                    "positions": o.get("eligible_positions") or o.get("positions"),
                }
            )
            if yid:
                overflow_by_player.setdefault(yid, []).append(day)
        total_overflow += len(overflow_rows)
        days_out.append(
            {
                "date": day,
                "weekday": _weekday_short(day),
                "is_today": day == today,
                "with_games": len(candidates),
                "startable": len(started_ids),
                "slot_cap": slot_cap,
                "overflow_count": len(overflow_rows),
                "has_overflow": len(overflow_rows) > 0,
                "overflow": overflow_rows,
            }
        )

    player_rows = []
    for p in enriched:
        yid = str(p.get("yahoo_id") or "")
        overflow_days = overflow_by_player.get(yid) or []
        player_rows.append(
            {
                "yahoo_id": yid,
                "db_id": p.get("db_id"),
                "name": p.get("full_name") or p.get("name"),
                "nba_team": p.get("nba_team"),
                "week_games": p.get("week_games") or 0,
                "overflow_days": overflow_days,
                "overflow_weekdays": [_weekday_short(d) for d in overflow_days],
                "overflow_count": len(overflow_days),
                "tonight_display": p.get("tonight_display"),
                "injury_status": p.get("injury_status"),
                "is_out": p.get("is_out"),
            }
        )

    overflow_days_n = sum(1 for d in days_out if d.get("has_overflow"))
    return {
        "today": today,
        "fetched_at": schedule.get("fetched_at"),
        "schedule_ok": bool(schedule.get("ok")),
        "schedule_errors": schedule.get("errors") or [],
        "slot_cap": slot_cap,
        "starter_slots": starter_slots,
        "week_label": week_label,
        "week": schedule.get("week"),
        "week_current": schedule.get("week_current"),
        "week_max": schedule.get("week_max"),
        "can_prev": schedule.get("can_prev"),
        "can_next": schedule.get("can_next"),
        "is_all_star": schedule.get("is_all_star"),
        "is_playoff": schedule.get("is_playoff"),
        "mode": schedule.get("mode"),
        "first_match": schedule.get("first_match"),
        "window_start": schedule.get("window_start"),
        "window_end": schedule.get("window_end"),
        "days": days_out,
        "players": player_rows,
        "summary": {
            "overflow_player_days": total_overflow,
            "days_with_overflow": overflow_days_n,
            "players_with_overflow": sum(1 for p in player_rows if p["overflow_count"]),
        },
        "by_yahoo_id": {
            str(p["yahoo_id"]): p for p in player_rows if p.get("yahoo_id")
        },
    }


def _roster_players_context() -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int], dict[str, Any]]:
    """Shared roster slot enrichment for page + lockout week API."""
    my_team = enrich_my_team_db_ids(load_json(config.MY_TEAM_PATH, {}) or {})
    slots = list(my_team.get("slots") or [])
    pool = list_players()
    by_yahoo = {str(p.get("yahoo_id")): p for p in pool if p.get("yahoo_id")}
    detail_slots: list[dict[str, Any]] = []
    db_ids: set[int] = set()
    roster_players: list[dict[str, Any]] = []
    seen: set[str] = set()

    for slot in slots:
        player = slot.get("player") or None
        enriched = None
        bucket = _group_bucket(slot.get("group"), slot.get("position"))
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
                "bucket": bucket,
            }
            if yahoo_id and yahoo_id not in seen:
                seen.add(yahoo_id)
                roster_players.append(dict(enriched))
        detail_slots.append(
            {
                "position": slot.get("position"),
                "label": slot.get("label") or slot.get("position"),
                "group": slot.get("group") or "starter",
                "bucket": bucket,
                "slot_index": slot.get("slot_index"),
                "player": enriched,
            }
        )

    injuries = _injuries_by_player_id(db_ids)
    for p in roster_players:
        try:
            pid = int(p["db_id"]) if p.get("db_id") is not None else None
        except (TypeError, ValueError):
            pid = None
        inj = injuries.get(pid) if pid else None
        if inj:
            p["injury_status"] = inj.get("injury_status")
            p["injury"] = inj.get("injury")

    return roster_players, detail_slots, db_ids, my_team


def load_lockouts(*, week: int | None = None) -> dict[str, Any]:
    """Bench lockouts for one week (for week navigation)."""
    roster_players, _, db_ids, _ = _roster_players_context()
    injuries = _injuries_by_player_id(db_ids)
    availability = _build_availability(roster_players, injuries, week=week)
    return {"ok": True, "availability": availability}


def load_my_roster_page(*, week: int | None = None) -> dict[str, Any]:
    roster_players, detail_slots, db_ids, my_team = _roster_players_context()
    pool = list_players()
    injuries = _injuries_by_player_id(db_ids)

    required = _slot_required_counts(detail_slots)
    coverage = _build_coverage(roster_players, required)
    contribution = _build_contribution(roster_players, pool)
    availability = _build_availability(roster_players, injuries, week=week)

    badges = contribution.get("badges_by_yahoo_id") or {}
    by_yahoo_avail = availability.get("by_yahoo_id") or {}
    for slot in detail_slots:
        p = slot.get("player")
        if not p:
            continue
        yid = str(p.get("yahoo_id") or "")
        p["cat_badges"] = badges.get(yid) or []
        hit = by_yahoo_avail.get(yid)
        if hit:
            p["week_games"] = hit.get("week_games")
            p["overflow_days"] = hit.get("overflow_days") or []
            p["overflow_weekdays"] = hit.get("overflow_weekdays") or []
            p["overflow_count"] = hit.get("overflow_count") or 0
            p["tonight_display"] = hit.get("tonight_display")
            p["injury_status"] = hit.get("injury_status") or p.get("injury_status")
            p["is_out"] = hit.get("is_out")

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
        "coverage": coverage,
        "availability": availability,
        "contribution": {
            "scope": contribution.get("scope"),
            "strength": contribution.get("strength"),
            "summary": contribution.get("summary"),
        },
        "news": news,
        "news_count": len(news),
        "player_count": filled,
    }
