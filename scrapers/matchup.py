"""Scrape current week matchups from Yahoo Fantasy."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

import config

_TEAM_HREF_RE = re.compile(
    rf"/nba/{re.escape(config.LEAGUE_ID)}/(\d+)(?:[/?#]|$)"
)

# Non-scoring display stats we skip when computing category wins
_SKIP_STATS = {"FGM/A", "FGM/A*", "FTM/A", "FTM/A*"}


def _parse_matchup_links(driver: WebDriver) -> tuple[int | None, list[dict[str, str]]]:
    week: int | None = None
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for a in driver.find_elements(By.CSS_SELECTOR, "a[href*='matchup']"):
        href = a.get_attribute("href") or ""
        if "mid1=" not in href or "mid2=" not in href:
            continue
        qs = parse_qs(urlparse(href).query)
        mid1 = (qs.get("mid1") or [None])[0]
        mid2 = (qs.get("mid2") or [None])[0]
        w = (qs.get("week") or [None])[0]
        if not mid1 or not mid2:
            continue
        if w and week is None:
            try:
                week = int(w)
            except ValueError:
                pass
        key = tuple(sorted((mid1, mid2)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(
            {
                "home_id": mid1,
                "away_id": mid2,
                "url": href.split("#")[0],
                "week": w or (str(week) if week else None),
            }
        )
    return week, pairs


def _team_name_map(teams: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(t.get("team_id")): t for t in teams if t.get("team_id") is not None}


def _parse_category_table(driver: WebDriver) -> list[dict[str, Any]]:
    """Parse #matchup-wall-header into per-stat rows."""
    tables = driver.find_elements(By.CSS_SELECTOR, "#matchup-wall-header table, #matchup-wall-header")
    if not tables:
        return []

    # Prefer actual table if nested
    table = None
    for el in driver.find_elements(By.CSS_SELECTOR, "#matchup-wall-header table"):
        table = el
        break
    if table is None:
        # fallback: first table on page that has FG%
        for el in driver.find_elements(By.CSS_SELECTOR, "table"):
            if "FG%" in (el.text or ""):
                table = el
                break
    if table is None:
        return []

    rows = table.find_elements(By.CSS_SELECTOR, "tr")
    if len(rows) < 3:
        return []

    headers = [c.text.strip() for c in rows[0].find_elements(By.CSS_SELECTOR, "th,td")]
    # Expected: Team, FG%, ..., TO  (maybe with FGM/A*)
    if not headers:
        return []

    team_rows = []
    for row in rows[1:]:
        cells = [c.text.strip() for c in row.find_elements(By.CSS_SELECTOR, "th,td")]
        if len(cells) < 2:
            continue
        # Skip notes row
        if cells[0].startswith("*") or "Non-scoring" in cells[0]:
            continue
        team_rows.append(cells)

    if len(team_rows) < 2:
        return []

    home_cells, away_cells = team_rows[0], team_rows[1]
    cats: list[dict[str, Any]] = []

    # Last column is often category win count (0)
    for i, header in enumerate(headers):
        if i == 0:
            continue
        h = header.replace("\xa0", " ").strip()
        if not h or h in _SKIP_STATS:
            continue
        # skip trailing empty / pure win-count column without a stat name
        home_val = home_cells[i] if i < len(home_cells) else ""
        away_val = away_cells[i] if i < len(away_cells) else ""
        if h.upper() in {"", "—"}:
            continue
        # The final unnamed? headers are named. Score column might be empty header
        if h.lower() in {"team"}:
            continue

        winner = None
        # Only call winner if both look numeric-ish (not "-" or "-/-")
        def _is_empty(v: str) -> bool:
            return v in {"", "-", "-/-", "–", "—"}

        if not _is_empty(home_val) and not _is_empty(away_val):
            try:
                # percentages / floats
                hv = float(home_val.replace("%", ""))
                av = float(away_val.replace("%", ""))
                # TO is lower-better
                if h.upper() in {"TO", "TURNOVERS"}:
                    if hv < av:
                        winner = "home"
                    elif av < hv:
                        winner = "away"
                else:
                    if hv > av:
                        winner = "home"
                    elif av > hv:
                        winner = "away"
            except ValueError:
                winner = None

        cats.append(
            {
                "stat": h,
                "home": home_val,
                "away": away_val,
                "winner": winner,
            }
        )

    # Drop trailing column if it looks like category tally duplicated (both ints, header weird)
    # Keep as-is; UI can show scoring cats from rules.
    return cats


def scrape_my_matchup_detail(driver: WebDriver, my_team_id: str | None) -> dict[str, Any]:
    url = (
        f"{config.LEAGUE_URL}/{my_team_id}/matchup"
        if my_team_id
        else f"{config.LEAGUE_URL}/matchup"
    )
    driver.get(url)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)

    # Names + category scores from big headers
    big = [
        e.text.strip()
        for e in driver.find_elements(By.CSS_SELECTOR, ".Fz-xxl")
        if (e.text or "").strip()
    ]
    # Pattern often: [HomeName, homeScore, awayScore, AwayName]
    home_name = big[0] if len(big) >= 1 else None
    away_name = big[-1] if len(big) >= 4 else (big[1] if len(big) == 2 else None)
    home_cats_won = None
    away_cats_won = None
    if len(big) >= 4 and big[1].isdigit() and big[2].isdigit():
        home_cats_won = int(big[1])
        away_cats_won = int(big[2])
        away_name = big[3]

    # Week label
    week_label = None
    body_text = driver.find_element(By.TAG_NAME, "body").text
    m = re.search(r"Week\s+(\d+)", body_text)
    if m:
        week_label = int(m.group(1))

    categories = _parse_category_table(driver)

    # Opponent / home ids from URL or links near header
    home_id = my_team_id
    away_id = None
    for a in driver.find_elements(By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]'):
        href = a.get_attribute("href") or ""
        text = (a.text or "").strip()
        mm = _TEAM_HREF_RE.search(href)
        if not mm:
            continue
        tid = mm.group(1)
        if away_name and text == away_name:
            away_id = tid
        if home_name and text == home_name:
            home_id = tid

    return {
        "week": week_label,
        "url": driver.current_url.split("#")[0],
        "home": {
            "team_id": home_id,
            "name": home_name,
            "categories_won": home_cats_won,
        },
        "away": {
            "team_id": away_id,
            "name": away_name,
            "categories_won": away_cats_won,
        },
        "categories": categories,
        "is_mine": True,
    }


def scrape_matchups(
    driver: WebDriver,
    *,
    teams: list[dict[str, Any]],
    my_team_id: str | None,
) -> dict[str, Any]:
    driver.get(config.LEAGUE_URL)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)

    week, pairs = _parse_matchup_links(driver)
    by_id = _team_name_map(teams)

    matchups: list[dict[str, Any]] = []
    for pair in pairs:
        home = by_id.get(pair["home_id"], {})
        away = by_id.get(pair["away_id"], {})
        is_mine = my_team_id in {pair["home_id"], pair["away_id"]}
        matchups.append(
            {
                "week": int(pair["week"]) if pair.get("week") and str(pair["week"]).isdigit() else week,
                "home": {
                    "team_id": pair["home_id"],
                    "name": home.get("name") or f"Team {pair['home_id']}",
                    "manager": home.get("manager") or "",
                    "logo": home.get("logo") or "",
                },
                "away": {
                    "team_id": pair["away_id"],
                    "name": away.get("name") or f"Team {pair['away_id']}",
                    "manager": away.get("manager") or "",
                    "logo": away.get("logo") or "",
                },
                "url": pair["url"],
                "is_mine": is_mine,
                "score": None,
            }
        )

    detail = scrape_my_matchup_detail(driver, my_team_id)

    # Enrich my row with category score from detail
    for mrow in matchups:
        if not mrow["is_mine"]:
            continue
        mrow["score"] = {
            "home": detail.get("home", {}).get("categories_won"),
            "away": detail.get("away", {}).get("categories_won"),
        }
        # Align names if missing
        if detail.get("home", {}).get("name"):
            # Ensure home/away orientation matches detail if possible
            if mrow["home"]["team_id"] == detail["home"].get("team_id"):
                pass
            elif mrow["away"]["team_id"] == detail["home"].get("team_id"):
                mrow["home"], mrow["away"] = mrow["away"], mrow["home"]
                if mrow["score"]:
                    mrow["score"] = {
                        "home": detail.get("home", {}).get("categories_won"),
                        "away": detail.get("away", {}).get("categories_won"),
                    }

    if week is None:
        week = detail.get("week")

    return {
        "league_id": config.LEAGUE_ID,
        "week": week,
        "matchups": matchups,
        "my_matchup": detail,
        "source_url": f"{config.LEAGUE_URL}/matchup",
    }
