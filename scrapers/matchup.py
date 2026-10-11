"""Scrape current week matchups from Yahoo Fantasy."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

import config
from jobs import interruptible_sleep, raise_if_cancelled

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


def _parse_my_matchup_page(
    driver: WebDriver, my_team_id: str | None, *, include_categories: bool = True
) -> dict[str, Any]:
    """Parse the currently loaded team matchup page."""
    parsed = driver.execute_script(
        """
        const big = [...document.querySelectorAll('.Fz-xxl')]
          .map((e) => (e.textContent || '').trim())
          .filter(Boolean);
        let homeName = big[0] || null;
        let awayName = big.length >= 4 ? big[3] : (big[1] || null);
        let homeCats = null, awayCats = null;
        if (big.length >= 4 && /^\\d+$/.test(big[1]) && /^\\d+$/.test(big[2])) {
          homeCats = Number(big[1]);
          awayCats = Number(big[2]);
          awayName = big[3];
        }
        const body = document.body.innerText || '';
        const wm = body.match(/Week\\s+(\\d+)/i);
        const week = wm ? Number(wm[1]) : null;
        const sel = document.querySelector('select');
        const weeks = sel
          ? [...sel.options].map((o) => {
              const tm = (o.textContent || '').match(/Week\\s+(\\d+)/i);
              const vm = String(o.value || '').match(/week=(\\d+)/i);
              return tm ? Number(tm[1]) : (vm ? Number(vm[1]) : null);
            }).filter((n) => Number.isFinite(n))
          : [];
        return { homeName, awayName, homeCats, awayCats, week, weeks };
        """
    ) or {}

    home_name = parsed.get("homeName")
    away_name = parsed.get("awayName")
    home_cats_won = parsed.get("homeCats")
    away_cats_won = parsed.get("awayCats")
    week_label = parsed.get("week")
    categories = _parse_category_table(driver) if include_categories else []

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

    # Ensure my team orientation: keep Yahoo's home/away as displayed.
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
        "available_weeks": list(parsed.get("weeks") or []),
    }


def scrape_my_matchup_detail(driver: WebDriver, my_team_id: str | None) -> dict[str, Any]:
    url = (
        f"{config.LEAGUE_URL}/{my_team_id}/matchup"
        if my_team_id
        else f"{config.LEAGUE_URL}/matchup"
    )
    raise_if_cancelled()
    driver.get(url)
    interruptible_sleep(config.PAGE_LOAD_WAIT_SEC)
    return _parse_my_matchup_page(driver, my_team_id, include_categories=True)


def scrape_my_season_schedule(
    driver: WebDriver,
    *,
    my_team_id: str | None,
    teams: list[dict[str, Any]] | None = None,
    current_week: int | None = None,
) -> list[dict[str, Any]]:
    """Scrape all weekly matchups for my team (opponent + cat score + categories)."""
    if not my_team_id:
        return []

    by_id = _team_name_map(teams or [])
    base = f"{config.LEAGUE_URL}/{my_team_id}/matchup"
    raise_if_cancelled()
    driver.get(base)
    interruptible_sleep(max(1.5, float(getattr(config, "PAGE_LOAD_WAIT_SEC", 4)) * 0.45))
    first = _parse_my_matchup_page(driver, my_team_id, include_categories=True)
    weeks = first.get("available_weeks") or []
    if not weeks and first.get("week"):
        weeks = [int(first["week"])]
    weeks = sorted({int(w) for w in weeks if w is not None})

    schedule: list[dict[str, Any]] = []
    wait = max(1.1, float(getattr(config, "PAGE_LOAD_WAIT_SEC", 4)) * 0.35)

    for week in weeks:
        raise_if_cancelled()
        if week == first.get("week"):
            detail = first
        else:
            driver.get(f"{base}?week={week}&module=matchup&mid1={my_team_id}")
            interruptible_sleep(wait)
            # Full cats for every week so dashboard nav can show them.
            detail = _parse_my_matchup_page(driver, my_team_id, include_categories=True)

        home = detail.get("home") or {}
        away = detail.get("away") or {}
        # Enrich logos/managers from league team list
        for side in (home, away):
            tid = str(side.get("team_id") or "")
            info = by_id.get(tid) or {}
            if info.get("manager") and not side.get("manager"):
                side["manager"] = info.get("manager")
            if info.get("logo"):
                side["logo"] = info.get("logo")
            if info.get("name") and not side.get("name"):
                side["name"] = info.get("name")

        w = detail.get("week") or week
        schedule.append(
            {
                "week": w,
                "url": detail.get("url"),
                "home": home,
                "away": away,
                "categories": detail.get("categories") or [],
                "score": {
                    "home": home.get("categories_won"),
                    "away": away.get("categories_won"),
                },
                "is_mine": True,
                "is_current": bool(current_week is not None and int(w) == int(current_week)),
            }
        )
        print(f"  my schedule week {w}: {home.get('name')} vs {away.get('name')}", flush=True)

    # Mark current if not set
    if current_week is not None:
        for row in schedule:
            row["is_current"] = int(row.get("week") or 0) == int(current_week)
    elif schedule:
        # Prefer the first page's week as current
        cur = first.get("week")
        for row in schedule:
            row["is_current"] = row.get("week") == cur

    schedule.sort(key=lambda r: int(r.get("week") or 0))
    return schedule


def scrape_matchups(
    driver: WebDriver,
    *,
    teams: list[dict[str, Any]],
    my_team_id: str | None,
) -> dict[str, Any]:
    raise_if_cancelled()
    driver.get(config.LEAGUE_URL)
    interruptible_sleep(config.PAGE_LOAD_WAIT_SEC)

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
    if week is None:
        week = detail.get("week")

    # Enrich my row with category score from detail
    for mrow in matchups:
        if not mrow["is_mine"]:
            continue
        mrow["score"] = {
            "home": detail.get("home", {}).get("categories_won"),
            "away": detail.get("away", {}).get("categories_won"),
        }
        if detail.get("home", {}).get("name"):
            if mrow["home"]["team_id"] == detail["home"].get("team_id"):
                pass
            elif mrow["away"]["team_id"] == detail["home"].get("team_id"):
                mrow["home"], mrow["away"] = mrow["away"], mrow["home"]
                if mrow["score"]:
                    mrow["score"] = {
                        "home": detail.get("home", {}).get("categories_won"),
                        "away": detail.get("away", {}).get("categories_won"),
                    }

    my_schedule = scrape_my_season_schedule(
        driver,
        my_team_id=my_team_id,
        teams=teams,
        current_week=int(week) if week is not None else None,
    )

    # Prefer schedule entry for current week as my_matchup (richer logos).
    current_mine = next((r for r in my_schedule if r.get("is_current")), None)
    if current_mine:
        detail = {
            **detail,
            "week": current_mine.get("week"),
            "url": current_mine.get("url") or detail.get("url"),
            "home": current_mine.get("home") or detail.get("home"),
            "away": current_mine.get("away") or detail.get("away"),
            "categories": current_mine.get("categories") or detail.get("categories"),
            "score": current_mine.get("score"),
            "is_mine": True,
        }

    return {
        "league_id": config.LEAGUE_ID,
        "week": week,
        "matchups": matchups,
        "my_matchup": detail,
        "my_schedule": my_schedule,
        "source_url": f"{config.LEAGUE_URL}/matchup",
    }
