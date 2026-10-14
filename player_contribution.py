"""Per-player category contribution (roster + FA) and custom FA scoring."""

from __future__ import annotations

import math
from typing import Any

import config
from categories import CATEGORIES, format_stat, mean, parse_stat
from db import get_player_by_yahoo_id, list_players
from decisions import resolve_weak_cats, _cat_rate, _is_available_owner, _team_abbr, _games_left_for_team
from nba_schedule import fetch_scoreboard_days
from store import load_json


def _std(values: list[float], avg: float) -> float:
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - avg) ** 2 for v in values) / len(values))


def _pool_baselines(pool: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        vals = [v for p in pool if (v := parse_stat(p.get(cat["field"]))) is not None]
        avg = mean(vals)
        if avg is None:
            continue
        out[cat["key"]] = {"mean": avg, "std": _std(vals, avg) or 1.0}
    return out


def _z(val: float | None, base: dict[str, float] | None, *, lower_better: bool) -> float | None:
    if val is None or not base:
        return None
    z = (val - base["mean"]) / (base["std"] or 1.0)
    return -z if lower_better else z


def _roster_players() -> list[dict[str, Any]]:
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    out = []
    seen: set[str] = set()
    for slot in my_team.get("slots") or []:
        p = slot.get("player")
        if not p:
            continue
        yid = str(p.get("yahoo_id") or "").strip()
        if not yid or yid in seen:
            continue
        seen.add(yid)
        db = get_player_by_yahoo_id(yid) or {}
        row = {
            "yahoo_id": yid,
            "db_id": db.get("id") or p.get("db_id"),
            "name": db.get("full_name") or p.get("name"),
            "nba_team": db.get("nba_team") or p.get("team"),
            "positions": db.get("positions") or p.get("positions"),
            "yahoo_rank": db.get("yahoo_rank"),
            "slot": slot.get("position"),
            "bucket": (
                "il"
                if str(slot.get("group") or "").lower() == "il"
                or str(slot.get("position") or "").upper() in {"IL", "IL+", "IR"}
                else "bench"
                if str(slot.get("group") or "").lower() == "bench"
                or str(slot.get("position") or "").upper() in {"BN", "BENCH"}
                else "active"
            ),
        }
        for cat in CATEGORIES:
            row[cat["field"]] = db.get(cat["field"])
        out.append(row)
    return out


def build_player_contributions(*, include_fa: bool = True, fa_limit: int = 40) -> dict[str, Any]:
    weak = resolve_weak_cats()
    weak_keys = set(weak.get("keys") or [])
    pool = [
        p
        for p in list_players()
        if any(parse_stat(p.get(c["field"])) is not None for c in CATEGORIES)
    ]
    baselines = _pool_baselines(pool)
    roster = _roster_players()

    # Team totals for % share (count cats only).
    team_totals: dict[str, float] = {}
    for cat in CATEGORIES:
        if cat["kind"] != "count":
            continue
        total = 0.0
        for p in roster:
            v = parse_stat(p.get(cat["field"]))
            if v is not None:
                total += v
        team_totals[cat["key"]] = total

    def enrich(players: list[dict[str, Any]], *, group: str) -> list[dict[str, Any]]:
        rows = []
        for p in players:
            cats_out = []
            saves = []
            hurts = []
            z_sum = 0.0
            z_n = 0
            for cat in CATEGORIES:
                val = parse_stat(p.get(cat["field"]))
                z = _z(val, baselines.get(cat["key"]), lower_better=bool(cat["lower_better"]))
                share = None
                if cat["kind"] == "count" and val is not None and team_totals.get(cat["key"]):
                    share = val / team_totals[cat["key"]]
                role = "neutral"
                if z is not None:
                    z_sum += z
                    z_n += 1
                    if z >= 0.6:
                        role = "saves"
                        if cat["key"] in weak_keys:
                            saves.append(cat["label"])
                    elif z <= -0.6:
                        role = "hurts"
                        if cat["key"] in weak_keys or cat["key"] == "tov":
                            hurts.append(cat["label"])
                cats_out.append(
                    {
                        "key": cat["key"],
                        "label": cat["label"],
                        "value": val,
                        "display": format_stat(val, kind=cat["kind"]),
                        "z": round(z, 2) if z is not None else None,
                        "team_share": round(share, 3) if share is not None else None,
                        "role": role,
                    }
                )
            rows.append(
                {
                    "yahoo_id": p.get("yahoo_id"),
                    "db_id": p.get("db_id") or p.get("id"),
                    "name": p.get("name") or p.get("full_name"),
                    "nba_team": p.get("nba_team"),
                    "positions": p.get("positions"),
                    "yahoo_rank": p.get("yahoo_rank"),
                    "slot": p.get("slot"),
                    "bucket": p.get("bucket"),
                    "group": group,
                    "avg_z": round(z_sum / z_n, 2) if z_n else None,
                    "saves": saves,
                    "hurts": hurts,
                    "categories": cats_out,
                }
            )
        rows.sort(key=lambda r: (-(r.get("avg_z") or -99), r.get("yahoo_rank") or 9999))
        return rows

    roster_rows = enrich(roster, group="roster")

    fa_rows: list[dict[str, Any]] = []
    if include_fa:
        fa_pool = []
        for p in pool:
            owner = p.get("fantasy_owner")
            if not _is_available_owner(owner):
                continue
            fa_pool.append(
                {
                    "yahoo_id": p.get("yahoo_id"),
                    "id": p.get("id"),
                    "full_name": p.get("full_name"),
                    "nba_team": p.get("nba_team"),
                    "positions": p.get("positions"),
                    "yahoo_rank": p.get("yahoo_rank"),
                    **{c["field"]: p.get(c["field"]) for c in CATEGORIES},
                }
            )
        fa_rows = enrich(fa_pool, group="fa")[: max(1, int(fa_limit))]

    return {
        "ok": True,
        "weak_keys": list(weak_keys),
        "weak_source": weak.get("source"),
        "roster": roster_rows,
        "free_agents": fa_rows,
        "note": "z-score vs local DB pool · % = share of your roster counting totals · "
        "saves/hurts flagged on weak cats.",
    }


def build_fa_custom_ranking(
    *,
    weights: dict[str, float] | None = None,
    prefer_positions: list[str] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """
    Weighted FA score from current weaknesses (auto) or explicit weights.
    weights keys = category keys (fg_pct, reb, …); positive = want more.
    """
    weak = resolve_weak_cats()
    auto_weights: dict[str, float] = {}
    for i, key in enumerate(weak.get("keys") or []):
        auto_weights[key] = 1.4 - i * 0.15
    if not auto_weights:
        try:
            from app_settings import load_app_settings

            auto_weights = dict(
                load_app_settings().get("fa_default_weights")
                or {"reb": 1.0, "blk": 1.0, "fg_pct": 0.8, "stl": 1.0}
            )
        except Exception:
            auto_weights = {"reb": 1.0, "blk": 1.0, "fg_pct": 0.8, "stl": 1.0}

    w = dict(auto_weights)
    if weights:
        for k, v in weights.items():
            try:
                w[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    # Drop zero/neg noise except explicit
    w = {k: float(v) for k, v in w.items() if v and float(v) != 0}

    prefer = {p.strip().upper() for p in (prefer_positions or []) if p and str(p).strip()}
    schedule = fetch_scoreboard_days()
    by_team = schedule.get("by_team") or {}
    today = str(schedule.get("today") or "")

    scored = []
    for p in list_players():
        yid = str(p.get("yahoo_id") or "").strip()
        if not yid or not _is_available_owner(p.get("fantasy_owner")):
            continue
        parts: dict[str, float] = {}
        total = 0.0
        for key, weight in w.items():
            rate = _cat_rate(p, key)
            if rate is None:
                continue
            part = rate * weight
            parts[key] = round(part, 3)
            total += part
        if total <= 0:
            continue
        abbr = _team_abbr(p.get("nba_team"))
        games, _ = _games_left_for_team(by_team, abbr, today=today)
        total *= 1.0 + min(games, 4) * 0.08
        pos = str(p.get("positions") or "").upper()
        pos_hit = bool(prefer) and any(tok in pos for tok in prefer)
        if pos_hit:
            total *= 1.15
        try:
            rank = int(p["yahoo_rank"]) if p.get("yahoo_rank") is not None else None
        except (TypeError, ValueError):
            rank = None
        scored.append(
            {
                "yahoo_id": yid,
                "db_id": p.get("id"),
                "name": p.get("full_name"),
                "nba_team": p.get("nba_team"),
                "positions": p.get("positions"),
                "yahoo_rank": rank,
                "owned_pct": p.get("owned_pct"),
                "status": p.get("fantasy_owner") or "FA",
                "games_left": games,
                "score": round(total, 2),
                "parts": parts,
                "pos_boost": pos_hit,
            }
        )

    scored.sort(key=lambda r: (-r["score"], r.get("yahoo_rank") or 9999))
    top = scored[: max(1, int(limit))]
    return {
        "ok": True,
        "weights": w,
        "prefer_positions": sorted(prefer),
        "weak_source": weak.get("source"),
        "count": len(top),
        "players": top,
        "note": "Custom FA score = Σ (cat utility × weight) × games boost × position boost.",
    }
