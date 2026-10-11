"""Free-agent shortlist: top 100 FA → score L7/L14 stats + Adds + L7 ranks → top 10."""

from __future__ import annotations

import re
from typing import Any, Callable

import config
from db import get_player_by_yahoo_id
from scrapers.players import discover_stat1_codes, ranked_players
from scrapers.trends import scrape_yahoo_transaction_trends
from store import load_json, save_json, utc_now_iso

ProgressCb = Callable[..., None]

POOL_N = 100
TOP_N = 10

# Category fantasy value (9-cat style); TO penalizes.
_STAT_WEIGHTS = {
    "pts": 1.0,
    "reb": 1.0,
    "ast": 1.0,
    "stl": 2.0,
    "blk": 2.0,
    "threes": 1.0,
    "to": -1.0,
}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "–", "—", "N/A"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _fantasy_value(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    total = 0.0
    used = 0
    for key, weight in _STAT_WEIGHTS.items():
        n = _num(row.get(key) if key != "to" else (row.get("to") or row.get("tov")))
        if n is None:
            continue
        total += n * weight
        used += 1
    if used < 3:
        return None
    return total


def _percentile_scores(
    values: dict[str, float | None],
    *,
    higher_is_better: bool,
) -> dict[str, float]:
    """Map id → 0..100 percentile among non-null values."""
    present = [(pid, v) for pid, v in values.items() if v is not None]
    if not present:
        return {}
    present.sort(key=lambda x: x[1], reverse=higher_is_better)
    n = len(present)
    out: dict[str, float] = {}
    if n == 1:
        out[present[0][0]] = 100.0
        return out
    for i, (pid, _) in enumerate(present):
        out[pid] = round(100.0 * (n - 1 - i) / (n - 1), 2)
    return out


def _slim_stats(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "gp": p.get("gp"),
        "rank": _num(p.get("rank")),
        "owned_pct": p.get("owned_pct"),
        "mpg": p.get("mpg"),
        "fg_pct": p.get("fg_pct"),
        "ft_pct": p.get("ft_pct"),
        "threes": p.get("threes"),
        "pts": p.get("pts"),
        "reb": p.get("reb"),
        "ast": p.get("ast"),
        "stl": p.get("stl"),
        "blk": p.get("blk"),
        "to": p.get("to"),
        "fantasy_value": _fantasy_value(p),
    }


def _index_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in rows:
        pid = str(p.get("player_id") or "").strip()
        if pid:
            out[pid] = p
    return out


def analyze_free_players(
    driver,
    *,
    pool: int = POOL_N,
    top: int = TOP_N,
    progress_cb: ProgressCb | None = None,
) -> dict[str, Any]:
    """Scrape available players and return top N by combined score."""
    pool = max(25, min(int(pool), 200))
    top = max(1, min(int(top), pool))
    steps = 5

    def emit(step: int, msg: str) -> None:
        if progress_cb:
            progress_cb(step, steps, msg)

    emit(0, "Detecting Yahoo date filters…")
    codes = discover_stat1_codes(driver)
    l7_code = codes.get("l7_avg") or "S_AL7"
    l14_code = codes.get("l14_avg") or "S_AL14"

    emit(1, f"Loading top {pool} available (season rank)…")
    base_rows = ranked_players(driver, limit=pool, status="A", sort="AR")
    base_by_id = _index_by_id(base_rows)

    emit(2, f"Loading Last 7 Days averages ({l7_code})…")
    l7_rows = ranked_players(
        driver, limit=pool, status="A", sort="AR", stat1=l7_code
    )
    l7_by_id = _index_by_id(l7_rows)

    emit(3, f"Loading Last 14 Days averages ({l14_code})…")
    l14_rows = ranked_players(
        driver, limit=pool, status="A", sort="AR", stat1=l14_code
    )
    l14_by_id = _index_by_id(l14_rows)

    emit(4, "Loading Research Adds…")
    adds_rows = scrape_yahoo_transaction_trends(driver, limit=max(50, pool))
    adds_by_id: dict[str, dict[str, Any]] = {}
    for row in adds_rows:
        pid = str(row.get("external_id") or "").strip()
        if not pid:
            continue
        # Prefer FA / waiver rows when duplicate external ids appear.
        prev = adds_by_id.get(pid)
        if prev is None:
            adds_by_id[pid] = row
            continue
        status = str(row.get("roster_status") or "")
        if status in {"FA", "W", "WA"} or status.startswith("W"):
            adds_by_id[pid] = row

    # Universe = season-AR top N (the “first 100” free agents).
    players: list[dict[str, Any]] = []
    l7_vals: dict[str, float | None] = {}
    l14_vals: dict[str, float | None] = {}
    adds_vals: dict[str, float | None] = {}
    rank_vals: dict[str, float | None] = {}

    for pid, base in base_by_id.items():
        l7 = l7_by_id.get(pid)
        l14 = l14_by_id.get(pid)
        adds_row = adds_by_id.get(pid)
        l7_stats = _slim_stats(l7) if l7 else None
        l14_stats = _slim_stats(l14) if l14 else None
        adds_n = _num(adds_row.get("adds")) if adds_row else None
        # Ranks (average last 7 days) ≈ Yahoo Current rank under L7 avg filter.
        rank_l7 = _num((l7 or {}).get("rank")) if l7 else None

        l7_vals[pid] = (l7_stats or {}).get("fantasy_value")
        l14_vals[pid] = (l14_stats or {}).get("fantasy_value")
        adds_vals[pid] = adds_n
        rank_vals[pid] = rank_l7

        db_row = get_player_by_yahoo_id(pid)
        players.append(
            {
                "player_id": pid,
                "db_id": db_row["id"] if db_row else None,
                "name": base.get("name"),
                "nba_team": base.get("nba_team"),
                "positions": base.get("positions"),
                "injury": base.get("injury"),
                "status": base.get("status"),
                "owned_pct": base.get("owned_pct"),
                "season_rank": _num(base.get("rank")),
                "profile_url": base.get("profile_url"),
                "l7": l7_stats,
                "l14": l14_stats,
                "adds": adds_n,
                "adds_total": _num(adds_row.get("total")) if adds_row else None,
                "rank_l7": rank_l7,
            }
        )

    s_l7 = _percentile_scores(l7_vals, higher_is_better=True)
    s_l14 = _percentile_scores(l14_vals, higher_is_better=True)
    s_adds = _percentile_scores(adds_vals, higher_is_better=True)
    s_rank = _percentile_scores(rank_vals, higher_is_better=False)

    for p in players:
        pid = p["player_id"]
        parts = {
            "l7_stats": s_l7.get(pid),
            "l14_stats": s_l14.get(pid),
            "adds": s_adds.get(pid),
            "rank_l7": s_rank.get(pid),
        }
        present = [v for v in parts.values() if v is not None]
        score = round(sum(present) / len(present), 2) if present else 0.0
        p["score_parts"] = parts
        p["score"] = score

    players.sort(
        key=lambda x: (
            -(x.get("score") or 0),
            x.get("rank_l7") is None,
            x.get("rank_l7") or 10_000,
            x.get("season_rank") or 10_000,
        )
    )
    for i, p in enumerate(players, start=1):
        p["pool_rank"] = i

    top_players = players[:top]
    scraped_at = utc_now_iso()
    payload = {
        "ok": True,
        "scraped_at": scraped_at,
        "pool": pool,
        "top": top,
        "stat1": {"l7_avg": l7_code, "l14_avg": l14_code},
        "weights": {
            "l7_stats": 1,
            "l14_stats": 1,
            "adds": 1,
            "rank_l7": 1,
        },
        "note": (
            "Score = average of percentiles (0–100) for L7 fantasy value, "
            "L14 fantasy value, Research Adds, and inverted L7 Yahoo rank."
        ),
        "players": top_players,
        "pool_players": players,
        "count": len(top_players),
        "pool_count": len(players),
    }
    save_json(config.FREE_PLAYERS_PATH, payload)
    emit(5, f"Done — top {len(top_players)} of {len(players)} free agents")
    return payload


def _attach_db_ids(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in players or []:
        row = dict(p)
        pid = str(row.get("player_id") or "").strip()
        db_row = get_player_by_yahoo_id(pid) if pid else None
        row["db_id"] = db_row["id"] if db_row else None
        out.append(row)
    return out


def load_free_players() -> dict[str, Any]:
    data = load_json(config.FREE_PLAYERS_PATH, None)
    if not data:
        return {
            "ok": True,
            "scraped_at": None,
            "players": [],
            "pool_players": [],
            "count": 0,
            "pool_count": 0,
            "note": "No Free Players analysis yet. Click Analyze.",
        }
    data = dict(data)
    data["players"] = _attach_db_ids(data.get("players") or [])
    # Keep pool light on disk; re-attach only if present.
    if data.get("pool_players"):
        data["pool_players"] = _attach_db_ids(data.get("pool_players") or [])
    return data


def sync_free_players(*, progress_cb: ProgressCb | None = None) -> dict[str, Any]:
    from chrome_browser import attach_chrome

    if progress_cb:
        progress_cb(0, 5, "Attaching Chrome…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass
    return analyze_free_players(driver, progress_cb=progress_cb)
