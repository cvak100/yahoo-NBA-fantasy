"""Category strength analytics for My Roster vs league player pool."""

from __future__ import annotations

import math
from typing import Any

import config
from categories import (
    CATEGORIES,
    format_stat,
    mean,
    parse_stat,
    strength_label,
)
from db import get_player_by_yahoo_id, list_players
from store import load_json

TOP_N = 5


def _analytics_top_n() -> int:
    try:
        from app_settings import load_app_settings

        n = int(load_app_settings().get("analytics_top_n") or TOP_N)
    except Exception:
        n = TOP_N
    return max(3, min(n, 15))


def _roster_players() -> list[dict[str, Any]]:
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in my_team.get("slots") or []:
        player = slot.get("player") or None
        if not player:
            continue
        yahoo_id = str(player.get("yahoo_id") or "").strip()
        if not yahoo_id or yahoo_id in seen:
            continue
        seen.add(yahoo_id)
        db_row = get_player_by_yahoo_id(yahoo_id) or {}
        out.append(
            {
                "yahoo_id": yahoo_id,
                "db_id": db_row.get("id"),
                "name": db_row.get("full_name") or player.get("name"),
                "nba_team": db_row.get("nba_team") or player.get("team"),
                "positions": db_row.get("positions") or player.get("positions"),
                "slot_position": slot.get("position"),
                "group": slot.get("group") or "starter",
            }
        )
    by_yahoo = {str(p.get("yahoo_id")): p for p in list_players() if p.get("yahoo_id")}
    for p in out:
        stats = by_yahoo.get(p["yahoo_id"]) or {}
        for cat in CATEGORIES:
            p[cat["field"]] = stats.get(cat["field"])
        p["db_id"] = p.get("db_id") or stats.get("id")
        p["gp"] = stats.get("gp")
        if stats.get("full_name"):
            p["name"] = stats["full_name"]
    return out


def _league_pool() -> list[dict[str, Any]]:
    return [
        p
        for p in list_players()
        if any(parse_stat(p.get(c["field"])) is not None for c in CATEGORIES)
    ]


def _rank_players(
    players: list[dict[str, Any]],
    *,
    field: str,
    kind: str,
    lower_better: bool,
    limit: int = TOP_N,
    include_slot: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in players:
        val = parse_stat(p.get(field))
        if val is None:
            continue
        row = {
            "db_id": p.get("db_id") or p.get("id"),
            "yahoo_id": p.get("yahoo_id"),
            "name": p.get("name") or p.get("full_name"),
            "nba_team": p.get("nba_team"),
            "positions": p.get("positions"),
            "value": val,
            "display": format_stat(val, kind=kind),
        }
        if include_slot:
            row["slot_position"] = p.get("slot_position")
        rows.append(row)
    rows.sort(key=lambda r: r["value"], reverse=not lower_better)
    return rows[: max(1, int(limit))]


def _std(values: list[float], avg: float) -> float:
    if len(values) < 2:
        return 0.0
    var = sum((v - avg) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _cat_baselines(pool: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Mean/std per category from the league pool (for MVP z-scores)."""
    out: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        vals = [v for p in pool if (v := parse_stat(p.get(cat["field"]))) is not None]
        avg = mean(vals)
        if avg is None:
            continue
        out[cat["key"]] = {"mean": avg, "std": _std(vals, avg)}
    return out


def _mvp_score(player: dict[str, Any], baselines: dict[str, dict[str, float]]) -> float | None:
    zs: list[float] = []
    for cat in CATEGORIES:
        base = baselines.get(cat["key"])
        if not base:
            continue
        val = parse_stat(player.get(cat["field"]))
        if val is None:
            continue
        std = base["std"]
        if std <= 1e-9:
            z = 0.0
        else:
            z = (val - base["mean"]) / std
        if cat["lower_better"]:
            z = -z
        zs.append(z)
    if not zs:
        return None
    return sum(zs) / len(zs)


def _rank_mvp(
    players: list[dict[str, Any]],
    baselines: dict[str, dict[str, float]],
    *,
    limit: int = TOP_N,
    include_slot: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in players:
        score = _mvp_score(p, baselines)
        if score is None:
            continue
        row = {
            "db_id": p.get("db_id") or p.get("id"),
            "yahoo_id": p.get("yahoo_id"),
            "name": p.get("name") or p.get("full_name"),
            "nba_team": p.get("nba_team"),
            "positions": p.get("positions"),
            "value": score,
            "display": f"{score:+.2f}",
        }
        if include_slot:
            row["slot_position"] = p.get("slot_position")
        rows.append(row)
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows[: max(1, int(limit))]


def load_analytics() -> dict[str, Any]:
    roster = _roster_players()
    pool = _league_pool()
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_yahoo_ids = {str(p.get("yahoo_id")) for p in roster if p.get("yahoo_id")}
    baselines = _cat_baselines(pool)
    top_n = _analytics_top_n()

    columns: list[dict[str, Any]] = []
    strength: list[dict[str, Any]] = []

    for cat in CATEGORIES:
        field = cat["field"]
        lower = bool(cat["lower_better"])
        kind = cat["kind"]

        my_vals = [v for p in roster if (v := parse_stat(p.get(field))) is not None]
        league_vals = [v for p in pool if (v := parse_stat(p.get(field))) is not None]
        my_avg = mean(my_vals)
        league_avg = mean(league_vals)
        label = strength_label(my_avg, league_avg, lower_better=lower)

        my_top = _rank_players(
            roster,
            field=field,
            kind=kind,
            lower_better=lower,
            limit=top_n,
            include_slot=True,
        )
        league_top = _rank_players(
            pool,
            field=field,
            kind=kind,
            lower_better=lower,
            limit=top_n,
            include_slot=False,
        )
        for row in league_top:
            row["on_my_roster"] = str(row.get("yahoo_id") or "") in my_yahoo_ids

        delta = None
        if my_avg is not None and league_avg is not None:
            delta = my_avg - league_avg

        col = {
            "key": cat["key"],
            "label": cat["label"],
            "kind": kind,
            "lower_better": lower,
            "my_avg": my_avg,
            "my_avg_display": format_stat(my_avg, kind=kind),
            "league_avg": league_avg,
            "league_avg_display": format_stat(league_avg, kind=kind),
            "delta": delta,
            "delta_display": format_stat(delta, kind=kind) if delta is not None else "—",
            "strength": label,
            "players": league_top,
        }
        columns.append(col)
        strength.append(
            {
                "key": cat["key"],
                "label": cat["label"],
                "strength": label,
                "my_avg_display": col["my_avg_display"],
                "league_avg_display": col["league_avg_display"],
                "my_top": my_top,
            }
        )

    # Overall MVP column (z-score average across all 9 cats vs league pool).
    my_mvp = _rank_mvp(roster, baselines, limit=top_n, include_slot=True)
    league_mvp = _rank_mvp(pool, baselines, limit=top_n, include_slot=False)
    for row in league_mvp:
        row["on_my_roster"] = str(row.get("yahoo_id") or "") in my_yahoo_ids

    my_mvp_scores = [r["value"] for r in my_mvp]
    league_mvp_scores = [
        s
        for p in pool
        if (s := _mvp_score(p, baselines)) is not None
    ]
    my_mvp_avg = mean(my_mvp_scores) if my_mvp_scores else mean(
        [s for p in roster if (s := _mvp_score(p, baselines)) is not None]
    )
    league_mvp_avg = mean(league_mvp_scores)
    mvp_strength = strength_label(my_mvp_avg, league_mvp_avg, lower_better=False)

    mvp_col = {
        "key": "mvp",
        "label": "MVP",
        "kind": "score",
        "lower_better": False,
        "my_avg": my_mvp_avg,
        "my_avg_display": f"{my_mvp_avg:+.2f}" if my_mvp_avg is not None else "—",
        "league_avg": league_mvp_avg,
        "league_avg_display": f"{league_mvp_avg:+.2f}" if league_mvp_avg is not None else "—",
        "delta": (my_mvp_avg - league_mvp_avg)
        if my_mvp_avg is not None and league_mvp_avg is not None
        else None,
        "delta_display": "—",
        "strength": mvp_strength,
        "players": league_mvp,
        "is_mvp": True,
    }
    if mvp_col["delta"] is not None:
        mvp_col["delta_display"] = f"{mvp_col['delta']:+.2f}"
    columns.append(mvp_col)
    strength.append(
        {
            "key": "mvp",
            "label": "MVP",
            "strength": mvp_strength,
            "my_avg_display": mvp_col["my_avg_display"],
            "league_avg_display": mvp_col["league_avg_display"],
            "my_top": my_mvp,
            "is_mvp": True,
        }
    )

    strong = sum(1 for s in strength if s["strength"] == "strong")
    weak = sum(1 for s in strength if s["strength"] == "weak")

    return {
        "ok": True,
        "team": {
            "team_id": my_team.get("team_id"),
            "name": my_team.get("name"),
            "logo": my_team.get("logo"),
            "player_count": len(roster),
        },
        "pool_count": len(pool),
        "top_n": top_n,
        "summary": {
            "strong": strong,
            "average": sum(1 for s in strength if s["strength"] == "average"),
            "weak": weak,
        },
        "strength": strength,
        "columns": columns,
        "note": (
            "MVP = average z-score across all 9 cats vs the league DB pool "
            "(TO inverted). Higher is better."
        ),
    }
