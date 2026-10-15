"""CBS Sports fantasy basketball depth charts (real starters by position).

Uses only the stdlib (no BeautifulSoup) so the app can start without extra deps.
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.request import Request, urlopen

import config
from store import load_json, save_json, utc_now_iso

ProgressCb = Callable[..., None]

CBS_DEPTH_PATH = config.DATA_DIR / "cbs_depth.json"
CBS_DEPTH_BASE = "https://www.cbssports.com/fantasy/basketball/depth-chart"
POSITIONS = ("PG", "SG", "SF", "PF", "C")

# CBS team codes → Yahoo / local NBA abbr
_TEAM_MAP = {
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
    "PHO": "PHX",
    "WSH": "WAS",
}

_ROW_RE = re.compile(
    r'<tr[^>]*\bTableBase-bodyTr\b[^>]*>(.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)
_TD_RE = re.compile(
    r'<td[^>]*\bTableBase-bodyTd\b[^>]*>(.*?)</td>',
    re.IGNORECASE | re.DOTALL,
)
_TEAM_RE = re.compile(r"/nba/teams/([A-Z]{2,3})/", re.IGNORECASE)
_PLAYER_LONG_RE = re.compile(
    r'<span[^>]*\bCellPlayerName--long\b[^>]*>.*?<a[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _fetch(url: str) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def scrape_position(position: str) -> list[dict[str, Any]]:
    pos = str(position or "").strip().upper()
    if pos not in POSITIONS:
        raise ValueError(f"Invalid position: {position}")
    url = f"{CBS_DEPTH_BASE}/{pos}/"
    html = _fetch(url)
    rows: list[dict[str, Any]] = []
    for row_html in _ROW_RE.findall(html):
        team_m = _TEAM_RE.search(row_html)
        if not team_m:
            continue
        code = team_m.group(1).upper()
        team = _TEAM_MAP.get(code, code)
        tds = _TD_RE.findall(row_html)
        if len(tds) < 2:
            continue
        # Col 0 = team, 1 = Starter, 2 = Backup, 3 = Reserves
        starter_links = _PLAYER_LONG_RE.findall(tds[1])
        if not starter_links:
            continue
        name = _strip_tags(starter_links[0])
        if not name:
            continue
        rows.append(
            {
                "nba_team": team,
                "position": pos,
                "depth_rank": 1,
                "name": name,
                "source": "cbs",
            }
        )
    return rows


def sync_cbs_depth(*, progress_cb: ProgressCb | None = None) -> dict[str, Any]:
    """Scrape PG–C depth charts from CBS and persist starters."""
    steps = len(POSITIONS)
    starters: list[dict[str, Any]] = []
    by_pos: dict[str, int] = {}

    for i, pos in enumerate(POSITIONS):
        if progress_cb:
            progress_cb(i, steps, f"CBS depth · {pos}…")
        rows = scrape_position(pos)
        by_pos[pos] = len(rows)
        starters.extend(rows)

    scraped_at = utc_now_iso()
    payload = {
        "ok": True,
        "scraped_at": scraped_at,
        "source": "cbsports",
        "source_url": CBS_DEPTH_BASE + "/",
        "positions": list(POSITIONS),
        "counts": by_pos,
        "count": len(starters),
        "starters": starters,
    }
    save_json(CBS_DEPTH_PATH, payload)
    if progress_cb:
        progress_cb(steps, steps, f"CBS depth done · {len(starters)} starters")
    return payload


def load_cbs_depth() -> dict[str, Any]:
    data = load_json(CBS_DEPTH_PATH, None)
    if not data:
        return {
            "ok": True,
            "scraped_at": None,
            "starters": [],
            "count": 0,
            "note": "No CBS depth chart yet — click Refresh starters.",
        }
    return dict(data)
