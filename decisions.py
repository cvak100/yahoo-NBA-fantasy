"""Decisions hub: weak cats, streaming targets, keep/cut, season deficit tracker."""

from __future__ import annotations

from typing import Any

import config
from analytics_view import load_analytics
from categories import CATEGORIES, parse_stat
from db import get_player_by_yahoo_id, list_players
from nba_schedule import fetch_scoreboard_days
from nba_teams import resolve_team
from store import load_json, save_json, utc_now_iso

# Yahoo matchup stat label → category key
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
_KEY_TO_STAT = {v: k for k, v in _STAT_TO_KEY.items() if k in {"FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"}}

CAT_HISTORY_PATH = getattr(config, "CAT_HISTORY_PATH", config.DATA_DIR / "cat_history.json")
STREAM_TOP_N = 10


def _streaming_limit(limit: int | None = None) -> int:
    if limit is not None:
        return max(1, min(int(limit), 50))
    try:
        from app_settings import load_app_settings

        n = int(load_app_settings().get("streaming_top_n") or STREAM_TOP_N)
    except Exception:
        n = STREAM_TOP_N
    return max(1, min(n, 50))


def _cat_by_key(key: str) -> dict[str, Any] | None:
    for c in CATEGORIES:
        if c["key"] == key:
            return c
    return None


def _my_team_id() -> str:
    my = load_json(config.MY_TEAM_PATH, {}) or {}
    meta = load_json(config.META_PATH, {}) or {}
    settings = load_json(config.APP_SETTINGS_PATH, {}) or {}
    return str(
        my.get("team_id")
        or meta.get("my_team_id")
        or settings.get("my_team_id")
        or ""
    ).strip()


def _side_for_me(entry: dict[str, Any], my_id: str) -> str | None:
    if not my_id:
        return None
    if str((entry.get("home") or {}).get("team_id") or "") == my_id:
        return "home"
    if str((entry.get("away") or {}).get("team_id") or "") == my_id:
        return "away"
    return None


def _result_for_me(winner: str | None, my_side: str | None) -> str | None:
    if not winner or not my_side:
        return None
    w = str(winner).lower()
    if w in {"tie", "draw"}:
        return "tie"
    if w == my_side:
        return "win"
    if w in {"home", "away"}:
        return "loss"
    return None


def _parse_margin(home_v: Any, away_v: Any, *, my_side: str, lower_better: bool) -> float | None:
    h = parse_stat(home_v)
    a = parse_stat(away_v)
    if h is None or a is None:
        return None
    mine = h if my_side == "home" else a
    theirs = a if my_side == "home" else h
    raw = mine - theirs
    return -raw if lower_better else raw


def _persist_cat_history(matchups: dict[str, Any], my_id: str) -> dict[str, Any]:
    """Merge finished week cat results into durable history."""
    hist = load_json(CAT_HISTORY_PATH, {}) or {}
    weeks: dict[str, Any] = dict(hist.get("weeks") or {})
    schedule = list(matchups.get("my_schedule") or [])
    mine = matchups.get("my_matchup") or {}
    if mine.get("home") or mine.get("away"):
        # Prefer live categories for current week.
        w = mine.get("week")
        entry = {
            "week": w,
            "home": mine.get("home"),
            "away": mine.get("away"),
            "categories": mine.get("categories") or [],
            "score": mine.get("score"),
        }
        if w is not None:
            replaced = False
            for i, s in enumerate(schedule):
                if s.get("week") == w:
                    schedule[i] = {**s, **entry}
                    replaced = True
                    break
            if not replaced:
                schedule.append(entry)

    for entry in schedule:
        week = entry.get("week")
        if week is None:
            continue
        my_side = _side_for_me(entry, my_id)
        if not my_side:
            continue
        cats_out: list[dict[str, Any]] = []
        decided = 0
        for raw in entry.get("categories") or []:
            stat = str(raw.get("stat") or "").strip()
            key = _STAT_TO_KEY.get(stat)
            if not key:
                continue
            cat = _cat_by_key(key)
            result = _result_for_me(raw.get("winner"), my_side)
            if result:
                decided += 1
            margin = _parse_margin(
                raw.get("home"),
                raw.get("away"),
                my_side=my_side,
                lower_better=bool((cat or {}).get("lower_better")),
            )
            cats_out.append(
                {
                    "key": key,
                    "stat": stat,
                    "result": result,
                    "margin": margin,
                    "home": raw.get("home"),
                    "away": raw.get("away"),
                    "winner": raw.get("winner"),
                }
            )
        if not cats_out:
            continue
        # Only store / upgrade when we have at least one decided cat, or replace empty.
        key = str(int(week))
        prev = weeks.get(key) or {}
        prev_decided = sum(1 for c in (prev.get("categories") or []) if c.get("result"))
        if decided >= prev_decided:
            opp = entry.get("away") if my_side == "home" else entry.get("home")
            weeks[key] = {
                "week": int(week),
                "my_side": my_side,
                "opponent": (opp or {}).get("name"),
                "opponent_id": str((opp or {}).get("team_id") or "") or None,
                "categories": cats_out,
                "decided": decided,
                "updated_at": utc_now_iso(),
            }

    hist = {
        "synced_at": utc_now_iso(),
        "my_team_id": my_id,
        "weeks": weeks,
    }
    save_json(CAT_HISTORY_PATH, hist)
    return hist


def build_deficit_tracker(my_id: str | None = None) -> dict[str, Any]:
    my_id = my_id or _my_team_id()
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    hist = _persist_cat_history(matchups, my_id) if my_id else (load_json(CAT_HISTORY_PATH, {}) or {})
    weeks_map: dict[str, Any] = dict(hist.get("weeks") or {})

    # Aggregates
    agg: dict[str, dict[str, Any]] = {
        c["key"]: {
            "key": c["key"],
            "label": c["label"],
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "undecided": 0,
            "margins": [],
            "weekly": [],  # {week, result, margin}
        }
        for c in CATEGORIES
    }

    week_rows: list[dict[str, Any]] = []
    for key in sorted(weeks_map.keys(), key=lambda x: int(x)):
        w = weeks_map[key]
        week_n = int(w.get("week") or key)
        w_wins = w_losses = w_ties = 0
        for c in w.get("categories") or []:
            ck = c.get("key")
            if ck not in agg:
                continue
            res = c.get("result")
            margin = c.get("margin")
            agg[ck]["weekly"].append(
                {"week": week_n, "result": res, "margin": margin}
            )
            if res == "win":
                agg[ck]["wins"] += 1
                w_wins += 1
            elif res == "loss":
                agg[ck]["losses"] += 1
                w_losses += 1
            elif res == "tie":
                agg[ck]["ties"] += 1
                w_ties += 1
            else:
                agg[ck]["undecided"] += 1
            if margin is not None:
                agg[ck]["margins"].append(float(margin))
        week_rows.append(
            {
                "week": week_n,
                "opponent": w.get("opponent"),
                "wins": w_wins,
                "losses": w_losses,
                "ties": w_ties,
                "decided": w.get("decided") or (w_wins + w_losses + w_ties),
            }
        )

    categories_out: list[dict[str, Any]] = []
    for c in CATEGORIES:
        a = agg[c["key"]]
        decided = a["wins"] + a["losses"] + a["ties"]
        win_pct = (a["wins"] + 0.5 * a["ties"]) / decided if decided else None
        avg_margin = (
            sum(a["margins"]) / len(a["margins"]) if a["margins"] else None
        )
        pain = None
        if decided:
            # Higher = hurts more (losses, low win%, negative margin).
            pain = round(
                (a["losses"] + 0.25 * a["ties"]) * 2
                + (0 if win_pct is None else (1 - win_pct) * 5)
                + (0 if avg_margin is None else max(0.0, -avg_margin) * 0.15),
                2,
            )
        categories_out.append(
            {
                "key": c["key"],
                "label": c["label"],
                "wins": a["wins"],
                "losses": a["losses"],
                "ties": a["ties"],
                "decided": decided,
                "win_pct": round(win_pct, 3) if win_pct is not None else None,
                "avg_margin": round(avg_margin, 3) if avg_margin is not None else None,
                "pain": pain,
                "weekly": a["weekly"],
                "radar": round((win_pct or 0.5) * 100, 1) if decided else None,
            }
        )

    categories_out.sort(
        key=lambda r: (
            r["pain"] is None,
            -(r["pain"] or 0),
            r["label"],
        )
    )
    decided_weeks = [w for w in week_rows if w["decided"]]
    return {
        "ok": True,
        "weeks_tracked": len(weeks_map),
        "weeks_decided": len(decided_weeks),
        "note": (
            None
            if decided_weeks
            else "No finished category results yet — tracker fills as weekly matchups complete."
        ),
        "categories": categories_out,
        "weeks": week_rows,
        "weak_keys": [
            r["key"]
            for r in categories_out
            if r.get("decided") and (r.get("win_pct") is not None) and r["win_pct"] < 0.45
        ][:4],
    }


def _matchup_weak_cats(my_id: str) -> list[dict[str, Any]]:
    """Cats you're losing or trailing in the current matchup."""
    matchups = load_json(config.MATCHUPS_PATH, {}) or {}
    mine = matchups.get("my_matchup") or {}
    my_side = _side_for_me(mine, my_id)
    if not my_side:
        return []
    out: list[dict[str, Any]] = []
    for raw in mine.get("categories") or []:
        stat = str(raw.get("stat") or "").strip()
        key = _STAT_TO_KEY.get(stat)
        cat = _cat_by_key(key) if key else None
        if not cat:
            continue
        result = _result_for_me(raw.get("winner"), my_side)
        margin = _parse_margin(
            raw.get("home"),
            raw.get("away"),
            my_side=my_side,
            lower_better=bool(cat["lower_better"]),
        )
        trailing = result == "loss" or (margin is not None and margin < 0)
        close = margin is not None and abs(margin) < (
            0.01 if cat["kind"] == "pct" else 3.0
        )
        if trailing or (result is None and close and margin is not None and margin <= 0):
            out.append(
                {
                    "key": key,
                    "label": cat["label"],
                    "stat": stat,
                    "result": result,
                    "margin": margin,
                    "source": "matchup",
                    "priority": 2 if result == "loss" else 1,
                }
            )
    out.sort(key=lambda r: (-r["priority"], r.get("margin") or 0))
    return out


def _analytics_weak_cats() -> list[dict[str, Any]]:
    try:
        data = load_analytics()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in data.get("strength") or []:
        if row.get("strength") != "weak":
            continue
        key = str(row.get("key") or "")
        out.append(
            {
                "key": key,
                "label": row.get("label") or key,
                "stat": _KEY_TO_STAT.get(key, key),
                "result": None,
                "margin": None,
                "source": "roster_strength",
                "priority": 1,
            }
        )
    return out


def resolve_weak_cats(my_id: str | None = None) -> dict[str, Any]:
    my_id = my_id or _my_team_id()
    matchup = _matchup_weak_cats(my_id) if my_id else []
    deficit = build_deficit_tracker(my_id)
    hist_weak = [
        {
            "key": c["key"],
            "label": c["label"],
            "stat": _KEY_TO_STAT.get(c["key"], c["label"]),
            "result": None,
            "margin": c.get("avg_margin"),
            "source": "season_deficit",
            "priority": 1,
            "win_pct": c.get("win_pct"),
        }
        for c in deficit.get("categories") or []
        if c.get("key") in set(deficit.get("weak_keys") or [])
    ]
    analytics = _analytics_weak_cats()

    # Prefer matchup → season deficit → analytics structural weakness.
    merged: dict[str, dict[str, Any]] = {}
    for src in (matchup, hist_weak, analytics):
        for row in src:
            key = str(row.get("key") or "")
            if not key or key in merged:
                continue
            merged[key] = row
    weak = list(merged.values())[:5]
    source = (
        "matchup"
        if matchup
        else "season_deficit"
        if hist_weak
        else "roster_strength"
        if analytics
        else "none"
    )
    keys = [w["key"] for w in weak]
    if not keys:
        # Preseason / empty matchup — volume cats for streaming defaults.
        defaults = [
            ("stl", "ST"),
            ("blk", "BLK"),
            ("threes", "3PTM"),
            ("reb", "REB"),
        ]
        weak = [
            {
                "key": k,
                "label": lab,
                "stat": lab,
                "result": None,
                "margin": None,
                "source": "default_volume",
                "priority": 0,
            }
            for k, lab in defaults
        ]
        keys = [k for k, _ in defaults]
        source = "default_volume"
    return {
        "ok": True,
        "source": source,
        "weak": weak,
        "keys": keys,
    }


def _team_abbr(raw: str | None) -> str | None:
    info = resolve_team(raw)
    if info:
        return str(info["abbr"])
    s = str(raw or "").strip().upper()
    return s or None


def _is_available_owner(owner: str | None) -> bool:
    o = str(owner or "").strip()
    if not o or o == "FA":
        return True
    if o in {"W", "WA"} or o.startswith("W ("):
        return True
    if o.lower() in {"taken", "you"}:
        return False
    # Named fantasy team = taken
    return False


def _games_left_for_team(
    by_team: dict[str, list[dict[str, Any]]],
    abbr: str | None,
    *,
    today: str,
) -> tuple[int, list[str]]:
    if not abbr:
        return 0, []
    games = by_team.get(abbr) or []
    left: list[str] = []
    for g in games:
        d = str(g.get("date") or "")
        state = str(g.get("state") or "").lower()
        if state == "post":
            continue
        if d < today:
            continue
        left.append(d)
    return len(left), left


def _cat_rate(player: dict[str, Any], key: str) -> float | None:
    cat = _cat_by_key(key)
    if not cat:
        return None
    val = parse_stat(player.get(cat["field"]))
    if val is None:
        return None
    if cat["lower_better"]:
        # Invert TO: lower is better → higher stream utility when small.
        return max(0.0, 5.0 - val)
    if cat["kind"] == "pct":
        v = val * 100 if val <= 1.0 else val
        return max(0.0, v - 40.0) / 10.0  # rough usefulness band
    return max(0.0, val)


def build_streaming_targets(*, limit: int | None = None) -> dict[str, Any]:
    limit = _streaming_limit(limit)
    my_id = _my_team_id()
    weak_info = resolve_weak_cats(my_id)
    weak_keys = list(weak_info.get("keys") or [])
    if not weak_keys:
        # Default volume cats if nothing else.
        weak_keys = ["stl", "blk", "threes", "reb"]

    schedule = fetch_scoreboard_days()
    by_team = schedule.get("by_team") or {}
    today = str(schedule.get("today") or "")

    # Candidate pool: DB available + free_players shortlist.
    pool_by_id: dict[str, dict[str, Any]] = {}
    for p in list_players():
        yid = str(p.get("yahoo_id") or "").strip()
        if not yid:
            continue
        owner = p.get("fantasy_owner")
        if not _is_available_owner(owner):
            continue
        pool_by_id[yid] = {
            "yahoo_id": yid,
            "db_id": p.get("id"),
            "name": p.get("full_name"),
            "nba_team": p.get("nba_team"),
            "positions": p.get("positions"),
            "yahoo_rank": p.get("yahoo_rank"),
            "owned_pct": p.get("owned_pct"),
            "status": owner or "FA",
            "fg_pct": p.get("fg_pct"),
            "ft_pct": p.get("ft_pct"),
            "threes": p.get("threes"),
            "pts": p.get("pts"),
            "reb": p.get("reb"),
            "ast": p.get("ast"),
            "stl": p.get("stl"),
            "blk": p.get("blk"),
            "tov": p.get("tov"),
        }

    fp = load_json(config.FREE_PLAYERS_PATH, {}) or {}
    for p in fp.get("players") or []:
        yid = str(p.get("player_id") or p.get("yahoo_id") or "").strip()
        if not yid:
            continue
        status = str(p.get("status") or "")
        if status and not _is_available_owner(status) and status.lower() != "taken":
            # free_players list is FA-oriented; keep unless clearly owned name
            pass
        db = get_player_by_yahoo_id(yid) or {}
        base = pool_by_id.get(yid) or {
            "yahoo_id": yid,
            "db_id": db.get("id"),
            "name": db.get("full_name") or p.get("name"),
            "nba_team": db.get("nba_team") or p.get("nba_team"),
            "positions": db.get("positions") or p.get("positions"),
            "yahoo_rank": db.get("yahoo_rank") or p.get("season_rank"),
            "owned_pct": db.get("owned_pct") or p.get("owned_pct"),
            "status": status or db.get("fantasy_owner") or "FA",
        }
        for cat in CATEGORIES:
            if base.get(cat["field"]) is None:
                base[cat["field"]] = db.get(cat["field"])
        # Prefer L7 rates when present.
        l7 = p.get("l7") or {}
        for cat in CATEGORIES:
            field = cat["field"]
            src = "stl" if field == "stl" else ("tov" if field == "tov" else field)
            if field == "tov":
                val = l7.get("to") or l7.get("tov")
            else:
                val = l7.get(src)
            if parse_stat(val) is not None:
                base[field] = val
        base["fa_score"] = p.get("score")
        pool_by_id[yid] = base

    scored: list[dict[str, Any]] = []
    for p in pool_by_id.values():
        abbr = _team_abbr(p.get("nba_team"))
        games_left, dates = _games_left_for_team(by_team, abbr, today=today)
        if games_left <= 0:
            continue
        fill_scores: dict[str, float] = {}
        fill_total = 0.0
        for key in weak_keys:
            rate = _cat_rate(p, key)
            if rate is None:
                continue
            contrib = rate * games_left
            fill_scores[key] = round(contrib, 2)
            fill_total += contrib
        if fill_total <= 0:
            continue
        rank = p.get("yahoo_rank")
        try:
            rank_i = int(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank_i = None
        rank_boost = max(0.0, (200 - rank_i) / 200.0) if rank_i else 0.2
        stream_score = round(fill_total * (0.85 + 0.15 * rank_boost), 2)
        why = []
        why.append(f"{games_left} game{'s' if games_left != 1 else ''} left")
        top_fills = sorted(fill_scores.items(), key=lambda x: -x[1])[:3]
        if top_fills:
            labels = []
            for k, _ in top_fills:
                cat = _cat_by_key(k)
                labels.append((cat or {}).get("label") or k)
            why.append("fills " + "/".join(labels))
        scored.append(
            {
                **{k: p.get(k) for k in (
                    "yahoo_id", "db_id", "name", "nba_team", "positions",
                    "yahoo_rank", "owned_pct", "status",
                )},
                "games_left": games_left,
                "game_dates": dates,
                "fill": fill_scores,
                "stream_score": stream_score,
                "why": why,
            }
        )

    scored.sort(key=lambda r: (-r["stream_score"], -r["games_left"], r.get("yahoo_rank") or 9999))
    top = scored[: max(1, int(limit))]
    return {
        "ok": True,
        "week_label": schedule.get("week_label"),
        "week": schedule.get("week"),
        "window_start": schedule.get("window_start"),
        "window_end": schedule.get("window_end"),
        "weak": weak_info.get("weak") or [],
        "weak_source": weak_info.get("source"),
        "count": len(top),
        "pool_scanned": len(pool_by_id),
        "targets": top,
        "note": (
            "Ranked by games left × contribution to your weak categories "
            f"(source: {weak_info.get('source')})."
        ),
    }


def _injury_flag(player: dict[str, Any]) -> str | None:
    # From roster slot injury / DB — soft parse.
    for key in ("injury", "injury_status", "status"):
        raw = str(player.get(key) or "").strip()
        if not raw:
            continue
        low = raw.lower()
        if low in {"o", "out"} or "out" in low:
            return "out"
        if "gtd" in low or "game time" in low or low in {"p", "probable", "questionable", "q"}:
            return "gtd"
        if raw not in {"FA", "W", "WA"} and not raw.startswith("W ("):
            if len(raw) <= 12 and raw.isalpha():
                return raw
    return None


def build_keep_or_cut() -> dict[str, Any]:
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    my_id = _my_team_id()
    weak = resolve_weak_cats(my_id)
    weak_keys = set(weak.get("keys") or [])
    schedule = fetch_scoreboard_days()
    by_team = schedule.get("by_team") or {}
    today = str(schedule.get("today") or "")

    # Position counts on full roster for redundancy.
    pos_counts: dict[str, int] = {}
    bench_il: list[dict[str, Any]] = []
    seen: set[str] = set()

    for slot in my_team.get("slots") or []:
        player = slot.get("player")
        if not player:
            continue
        yid = str(player.get("yahoo_id") or "").strip()
        pos = str(slot.get("position") or "").upper()
        group = str(slot.get("group") or "").lower()
        bucket = "il" if group == "il" or pos in {"IL", "IL+", "IR"} else (
            "bench" if group == "bench" or pos in {"BN", "BENCH"} else "active"
        )
        db = get_player_by_yahoo_id(yid) if yid else None
        positions = str((db or {}).get("positions") or player.get("positions") or "")
        for tok in positions.replace("/", ",").split(","):
            t = tok.strip().upper()
            if t:
                pos_counts[t] = pos_counts.get(t, 0) + 1
        if bucket not in {"bench", "il"}:
            continue
        if not yid or yid in seen:
            continue
        seen.add(yid)
        row = {
            "yahoo_id": yid,
            "db_id": (db or {}).get("id") or player.get("db_id"),
            "name": (db or {}).get("full_name") or player.get("name"),
            "nba_team": (db or {}).get("nba_team") or player.get("team"),
            "positions": positions,
            "slot": pos or bucket.upper(),
            "bucket": bucket,
            "yahoo_rank": (db or {}).get("yahoo_rank"),
            "owned_pct": (db or {}).get("owned_pct"),
            "injury": player.get("injury") or _injury_flag(player),
            "fg_pct": (db or {}).get("fg_pct"),
            "ft_pct": (db or {}).get("ft_pct"),
            "threes": (db or {}).get("threes"),
            "pts": (db or {}).get("pts"),
            "reb": (db or {}).get("reb"),
            "ast": (db or {}).get("ast"),
            "stl": (db or {}).get("stl"),
            "blk": (db or {}).get("blk"),
            "tov": (db or {}).get("tov"),
        }
        bench_il.append(row)

    results: list[dict[str, Any]] = []
    for p in bench_il:
        reasons: list[str] = []
        score = 50  # neutral hold baseline; map to label later
        abbr = _team_abbr(p.get("nba_team"))
        games_left, _dates = _games_left_for_team(by_team, abbr, today=today)

        try:
            rank = int(p["yahoo_rank"]) if p.get("yahoo_rank") is not None else None
        except (TypeError, ValueError):
            rank = None
        owned = parse_stat(p.get("owned_pct"))

        if rank is not None:
            if rank <= 40:
                score += 25
                reasons.append(f"elite Current rank #{rank}")
            elif rank <= 90:
                score += 12
                reasons.append(f"solid rank #{rank}")
            elif rank >= 160:
                score -= 18
                reasons.append(f"fringe rank #{rank}")
            else:
                reasons.append(f"rank #{rank}")

        if owned is not None:
            if owned >= 70:
                score += 8
                reasons.append(f"high owned ({owned:g}%) — trade/stream value")
            elif owned <= 15:
                score -= 8
                reasons.append(f"low owned ({owned:g}%)")

        inj = (p.get("injury") or "")
        if isinstance(inj, str) and inj:
            low = inj.lower()
            if "out" in low or low == "o":
                if p["bucket"] == "il":
                    score += 6
                    reasons.append("IL stash (out)")
                else:
                    score -= 10
                    reasons.append("out — burning a BN spot")
            elif "gtd" in low or "questionable" in low:
                reasons.append(f"injury: {inj}")

        if games_left >= 3:
            score += 8
            reasons.append(f"{games_left} games left this week")
        elif games_left == 0 and p["bucket"] == "bench":
            score -= 12
            reasons.append("no games left this week")

        # Weak-cat fit
        fills = []
        for key in weak_keys:
            rate = _cat_rate(p, key)
            if rate is not None and rate > 0.8:
                cat = _cat_by_key(key)
                fills.append((cat or {}).get("label") or key)
        if fills:
            score += 10
            reasons.append("helps weak " + "/".join(fills[:3]))

        # Redundancy: many same primary pos
        primary = (str(p.get("positions") or "").split(",")[0] or "").strip().upper()
        if primary and pos_counts.get(primary, 0) >= 4 and (rank or 999) > 100:
            score -= 10
            reasons.append(f"crowded at {primary}")

        if p["bucket"] == "il" and rank is not None and rank <= 80:
            score += 10
            reasons.append("quality IL stash")

        # Clamp and label — explanation-first, not fake precision.
        score = max(0, min(100, score))
        if score >= 68:
            verdict = "keep"
        elif score <= 38:
            verdict = "cut"
        else:
            verdict = "hold"

        results.append(
            {
                "yahoo_id": p.get("yahoo_id"),
                "db_id": p.get("db_id"),
                "name": p.get("name"),
                "nba_team": p.get("nba_team"),
                "positions": p.get("positions"),
                "slot": p.get("slot"),
                "bucket": p.get("bucket"),
                "yahoo_rank": rank,
                "owned_pct": p.get("owned_pct"),
                "games_left": games_left,
                "verdict": verdict,
                "confidence": "high" if score >= 75 or score <= 30 else "medium",
                "reasons": reasons[:4],
                "score_hint": score,  # internal-ish; UI can hide
            }
        )

    order = {"cut": 0, "hold": 1, "keep": 2}
    results.sort(key=lambda r: (order.get(r["verdict"], 9), r.get("yahoo_rank") or 9999))
    return {
        "ok": True,
        "count": len(results),
        "weak_keys": list(weak_keys),
        "players": results,
        "note": "Labels are Keep / Hold / Cut with reasons — not a black-box 0–100 grade.",
    }


def load_decisions_page() -> dict[str, Any]:
    my_id = _my_team_id()
    weak = resolve_weak_cats(my_id)
    deficit = build_deficit_tracker(my_id)
    streaming = build_streaming_targets()
    keepcut = build_keep_or_cut()
    return {
        "ok": True,
        "synced_at": utc_now_iso(),
        "my_team_id": my_id or None,
        "weak": weak,
        "deficit": deficit,
        "streaming": streaming,
        "keep_cut": keepcut,
    }
