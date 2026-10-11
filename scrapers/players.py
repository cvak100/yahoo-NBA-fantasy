"""Scrape Yahoo Fantasy player search and available players."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote_plus

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config

_PLAYER_ID_RE = re.compile(r"/nba/players/(\d+)")
_APID_RE = re.compile(r"[?&]apid=(\d+)")
_INJURY_FLAGS = {"Q", "O", "D", "NA", "P", "GTD", "OUT", "INJ"}


def _strip_injury(name_raw: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)([A-Z]{1,3})$", name_raw.strip())
    if m and m.group(2) in _INJURY_FLAGS:
        return m.group(1).strip(), m.group(2)
    return name_raw.strip(), None


def _parse_meta(text: str) -> tuple[str, str]:
    meta = (text or "").strip()
    if " - " in meta:
        team, pos = meta.split(" - ", 1)
        return team.strip(), pos.strip()
    return meta, ""


def _cell_texts(row) -> list[str]:
    return [td.text.strip() for td in row.find_elements(By.CSS_SELECTOR, "td")]


def _guess_stat_slice(texts: list[str]) -> dict[str, str]:
    """
    Find FA/W owner status, then GP / ranks / owned / stats.
    Works across slightly different Yahoo table layouts.
    """
    status_idx = None
    for i, t in enumerate(texts):
        if t in {"FA", "W", "WA"} or t.startswith("W ("):
            status_idx = i
            break
    if status_idx is None:
        # Owned by a fantasy team: cell before GP (integer) + ranks
        for i, t in enumerate(texts):
            if (
                i + 3 < len(texts)
                and re.fullmatch(r"\d+", texts[i + 1] or "")
                and re.fullmatch(r"\d+", texts[i + 2] or "")
                and (texts[i + 3] or "").endswith("%")
                and t
                and t not in {"", "Notes"}
            ):
                status_idx = i
                break

    out: dict[str, str] = {
        "status": "FA",
        "gp": "",
        "rank_preseason": "",
        "rank": "",
        "owned_pct": "",
        "mpg": "",
        "fg_pct": "",
        "ft_pct": "",
        "threes": "",
        "pts": "",
        "reb": "",
        "ast": "",
        "stl": "",
        "blk": "",
        "to": "",
    }
    if status_idx is None:
        return out

    out["status"] = texts[status_idx]
    rest = texts[status_idx + 1 :]

    # Players list: GP, Pre-Season, Current, %Ros, MPG, ...
    # Search list:   GP, Current, %Ros, MPG, ... (no preseason)
    if len(rest) >= 3 and rest[2].endswith("%"):
        keys = [
            "gp",
            "rank",
            "owned_pct",
            "mpg",
            "fgm_a",
            "fg_pct",
            "ftm_a",
            "ft_pct",
            "threes",
            "pts",
            "reb",
            "ast",
            "stl",
            "blk",
            "to",
        ]
    else:
        keys = [
            "gp",
            "rank_preseason",
            "rank",
            "owned_pct",
            "mpg",
            "fgm_a",
            "fg_pct",
            "ftm_a",
            "ft_pct",
            "threes",
            "pts",
            "reb",
            "ast",
            "stl",
            "blk",
            "to",
        ]
    for key, val in zip(keys, rest):
        if key in out:
            out[key] = val
    return out


def _parse_players_table(driver: WebDriver, *, limit: int | None = None) -> list[dict[str, Any]]:
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#players-table")))
    table = driver.find_element(By.CSS_SELECTOR, "#players-table")
    players: list[dict[str, Any]] = []

    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        links = row.find_elements(By.CSS_SELECTOR, "a[href*='/nba/players/']")
        if not links:
            continue
        name_link = links[0]
        name_raw = (name_link.text or "").strip()
        if not name_raw:
            continue
        name, injury = _strip_injury(name_raw)
        profile = name_link.get_attribute("href")
        m = _PLAYER_ID_RE.search(profile or "")
        player_id = m.group(1) if m else None

        # Team/pos often in same cell after the link, or next cell
        nba_team, positions = "", ""
        name_td = name_link.find_element(By.XPATH, "./ancestor::td[1]")
        full = name_td.text.strip()
        lines = [ln.strip() for ln in full.splitlines() if ln.strip()]
        if len(lines) >= 2:
            nba_team, positions = _parse_meta(lines[1])
        else:
            # search layout: next td is "HOU - SG,SF,PF"
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            try:
                idx = cells.index(name_td)
            except ValueError:
                idx = -1
            if idx >= 0 and idx + 1 < len(cells):
                nxt = cells[idx + 1].text.strip()
                if " - " in nxt or re.fullmatch(r"[A-Z]{2,3}", nxt):
                    nba_team, positions = _parse_meta(nxt)

        texts = _cell_texts(row)
        stats = _guess_stat_slice(texts)
        status = stats["status"]
        available = status in {"FA", "W", "WA"} or status.startswith("W")

        players.append(
            {
                "player_id": player_id,
                "name": name,
                "nba_team": nba_team,
                "positions": positions,
                "injury": injury,
                "status": status,
                "gp": stats["gp"],
                "rank_preseason": stats["rank_preseason"],
                "rank": stats["rank"],
                "owned_pct": stats["owned_pct"],
                "mpg": stats["mpg"],
                "fg_pct": stats["fg_pct"],
                "ft_pct": stats["ft_pct"],
                "threes": stats["threes"],
                "pts": stats["pts"],
                "reb": stats["reb"],
                "ast": stats["ast"],
                "stl": stats["stl"],
                "blk": stats["blk"],
                "to": stats["to"],
                "profile_url": profile,
                "available": available,
            }
        )
        if limit and len(players) >= limit:
            break

    return players


def search_players(driver: WebDriver, query: str, *, limit: int = 25) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"query": q, "players": [], "count": 0, "source_url": None}

    url = f"{config.LEAGUE_URL}/playersearch?search={quote_plus(q)}"
    driver.get(url)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    players = _parse_players_table(driver, limit=limit)
    return {
        "query": q,
        "players": players,
        "count": len(players),
        "source_url": driver.current_url.split("#")[0],
    }


def discover_stat1_codes(driver: WebDriver) -> dict[str, str]:
    """Read Yahoo players page dropdown → codes for Last 7 / Last 14 day averages."""
    url = f"{config.LEAGUE_URL}/players?status=A&sort=AR&jsenabled=1"
    driver.get(url)
    time.sleep(max(1.5, float(config.PAGE_LOAD_WAIT_SEC) * 0.5))
    options = driver.execute_script(
        """
        const out = [];
        for (const sel of document.querySelectorAll('select')) {
          for (const opt of sel.options || []) {
            const v = (opt.value || '').trim();
            const t = (opt.textContent || '').trim();
            if (v && t) out.push({ value: v, text: t });
          }
        }
        return out;
        """
    ) or []
    found: dict[str, str] = {}
    for opt in options:
        text = str(opt.get("text") or "").lower()
        value = str(opt.get("value") or "").strip()
        if not value:
            continue
        if "last 7" in text and "avg" in text and "std" not in text:
            found.setdefault("l7_avg", value)
        elif "last 14" in text and "avg" in text and "std" not in text:
            found.setdefault("l14_avg", value)
        elif "last 7" in text and "total" in text and "std" not in text:
            found.setdefault("l7_total", value)
        elif "last 14" in text and "total" in text and "std" not in text:
            found.setdefault("l14_total", value)
    # Fallbacks seen across Yahoo fantasy seasons
    found.setdefault("l7_avg", "S_AL7")
    found.setdefault("l14_avg", "S_AL14")
    return found


def ranked_players(
    driver: WebDriver,
    *,
    limit: int = 25,
    status: str = "ALL",
    sort: str = "AR",
    stat1: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch players across Yahoo pages (25 per page via count=offset)."""
    players: list[dict[str, Any]] = []
    page_size = 25
    offset = 0
    seen: set[str] = set()
    empty_pages = 0

    # Yahoo pages are 25 players; ~800–900 NBA names → keep scanning past 500.
    max_offset = max(2000, int(limit) + 100)
    while len(players) < limit and offset < max_offset:
        url = (
            f"{config.LEAGUE_URL}/players?status={quote_plus(status)}"
            f"&sort={quote_plus(sort)}&count={offset}&jsenabled=1"
        )
        if stat1:
            url += f"&stat1={quote_plus(stat1)}"
        driver.get(url)
        time.sleep(config.PAGE_LOAD_WAIT_SEC)
        page = _parse_players_table(driver, limit=page_size)
        if not page:
            empty_pages += 1
            if empty_pages >= 2:
                break
            offset += page_size
            continue
        empty_pages = 0
        added = 0
        for p in page:
            pid = str(p.get("player_id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            players.append(p)
            added += 1
            if len(players) >= limit:
                break
        print(f"  ranked page count={offset}: +{added} (total {len(players)})", flush=True)
        # Always advance; Yahoo may return <25 parseable rows on a full page.
        if added == 0:
            break
        offset += page_size

    return players[:limit]


def top_available(
    driver: WebDriver,
    *,
    limit: int = 15,
    sort: str = "AR",
) -> dict[str, Any]:
    """Top available free agents by Yahoo Average Rank."""
    players = ranked_players(driver, limit=limit, status="A", sort=sort)
    pick = players[0] if players else None
    return {
        "sort": sort,
        "status": "A",
        "players": players,
        "smart_pick": pick,
        "count": len(players),
        "source_url": driver.current_url.split("#")[0],
        "note": "Best available by Yahoo Average Rank among free agents.",
    }
