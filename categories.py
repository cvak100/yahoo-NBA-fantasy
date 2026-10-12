"""League H2H category definitions (9 cats) and numeric helpers."""

from __future__ import annotations

import re
from typing import Any

# Yahoo Labels → local season_avg field on players / player_stats.
CATEGORIES: list[dict[str, Any]] = [
    {"key": "fg_pct", "label": "FG%", "field": "fg_pct", "kind": "pct", "lower_better": False},
    {"key": "ft_pct", "label": "FT%", "field": "ft_pct", "kind": "pct", "lower_better": False},
    {"key": "threes", "label": "3PTM", "field": "threes", "kind": "count", "lower_better": False},
    {"key": "pts", "label": "PTS", "field": "pts", "kind": "count", "lower_better": False},
    {"key": "reb", "label": "REB", "field": "reb", "kind": "count", "lower_better": False},
    {"key": "ast", "label": "AST", "field": "ast", "kind": "count", "lower_better": False},
    {"key": "stl", "label": "ST", "field": "stl", "kind": "count", "lower_better": False},
    {"key": "blk", "label": "BLK", "field": "blk", "kind": "count", "lower_better": False},
    {"key": "tov", "label": "TO", "field": "tov", "kind": "count", "lower_better": True},
]


def parse_stat(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in {"-", "–", "—", "N/A", "n/a"}:
        return None
    s = s.replace(",", "").replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def strength_label(mine: float | None, league: float | None, *, lower_better: bool) -> str:
    if mine is None or league is None:
        return "unknown"
    if league == 0:
        if mine == 0:
            return "average"
        return "weak" if lower_better else "strong"
    # Relative gap vs league mean.
    rel = (mine - league) / abs(league)
    if lower_better:
        rel = -rel
    if rel >= 0.08:
        return "strong"
    if rel <= -0.08:
        return "weak"
    return "average"


def compare_winner(mine: float | None, theirs: float | None, *, lower_better: bool) -> str | None:
    if mine is None or theirs is None:
        return None
    if abs(mine - theirs) < 1e-9:
        return "tie"
    mine_better = mine < theirs if lower_better else mine > theirs
    return "mine" if mine_better else "theirs"


def format_stat(value: float | None, *, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        # season_avg may already be 0-1 or 0-100
        v = value * 100 if value <= 1.0 else value
        return f"{v:.1f}%"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.1f}"
