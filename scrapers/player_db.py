"""Build local player DB from Yahoo Fantasy (top N players)."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote_plus

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import Select

import config
from db import (
    connect,
    content_hash,
    insert_depth_if_new,
    insert_stat_if_new,
    upsert_player,
)
from jobs import JobCancelled, interruptible_sleep, raise_if_cancelled, restore_page_load_timeout
from scrapers.players import _parse_players_table, ranked_players, top_available
from store import utc_now_iso

_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def primary_position(positions: str | None) -> str:
    if not positions:
        return ""
    return positions.split(",")[0].strip()


def _nba_team_to_eteam(driver: WebDriver) -> dict[str, str]:
    """Map visible team name / abbreviation helpers via select values."""
    driver.get(f"{config.LEAGUE_URL}/players")
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    sel = Select(driver.find_element(By.CSS_SELECTOR, "select[name='eteam']"))
    # value -> label
    label_by_val = {o.get_attribute("value"): o.text.strip() for o in sel.options}
    # common abbreviations used on Yahoo player rows
    abbr_to_label = {
        "ATL": "Atlanta",
        "BOS": "Boston",
        "BKN": "Brooklyn",
        "CHA": "Charlotte",
        "CHI": "Chicago",
        "CLE": "Cleveland",
        "DAL": "Dallas",
        "DEN": "Denver",
        "DET": "Detroit",
        "GSW": "Golden State",
        "HOU": "Houston",
        "IND": "Indiana",
        "LAC": "LA Clippers",
        "LAL": "LA Lakers",
        "MEM": "Memphis",
        "MIA": "Miami",
        "MIL": "Milwaukee",
        "MIN": "Minnesota",
        "NOP": "New Orleans",
        "NYK": "New York",
        "OKC": "Oklahoma City",
        "ORL": "Orlando",
        "PHI": "Philadelphia",
        "PHX": "Phoenix",
        "POR": "Portland",
        "SAC": "Sacramento",
        "SAS": "San Antonio",
        "TOR": "Toronto",
        "UTA": "Utah",
        "WAS": "Washington",
    }
    label_to_val = {v: k for k, v in label_by_val.items() if k and k != "ALL"}
    out: dict[str, str] = {}
    for abbr, label in abbr_to_label.items():
        if label in label_to_val:
            out[abbr] = label_to_val[label]
    return out


def _open_playernote(driver: WebDriver, yahoo_id: str) -> None:
    # Prefer notes icon; fallback to player name playernote link
    selectors = [
        f"a.ysf-player-icon-notes[data-ys-playerid='{yahoo_id}']",
        f"a.playernote-recent[data-ys-playerid='{yahoo_id}']",
        f"a.playernote-new[data-ys-playerid='{yahoo_id}']",
        f"a[data-ys-playerid='{yahoo_id}'].playernote-recent",
        f"a[data-ys-playerid='{yahoo_id}'].playernote-new",
        f"#playernote-{yahoo_id}",
        f"a.playernote[data-ys-playerid='{yahoo_id}']",
    ]
    icons = []
    for sel in selectors:
        icons = driver.find_elements(By.CSS_SELECTOR, sel)
        if icons:
            break
    if not icons:
        raise RuntimeError(f"No playernote control for yahoo_id={yahoo_id}")
    driver.execute_script("arguments[0].click();", icons[0])
    interruptible_sleep(3.5)


def _close_playernote(driver: WebDriver) -> None:
    for sel in [
        "#react-playernote-overlay .Close",
        "#react-playernote-overlay button[title='Close']",
        ".Overlay .Close",
        "button.Close",
    ]:
        btns = driver.find_elements(By.CSS_SELECTOR, sel)
        if btns:
            try:
                driver.execute_script("arguments[0].click();", btns[0])
                interruptible_sleep(1)
                return
            except Exception:
                pass
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys("\ue00c")  # ESC
    except Exception:
        pass


def _click_overlay_tab(driver: WebDriver, label: str) -> None:
    """Click an overlay tab. Prefer the inner <button> — clicking <li> often does nothing."""
    root = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
    targets = []
    for el in root.find_elements(By.CSS_SELECTOR, "button, [role='tab'], a, li"):
        if (el.text or "").strip() != label:
            continue
        targets.append(el)
    # Prefer buttons / role=tab over wrapping <li>
    targets.sort(key=lambda e: 0 if (e.tag_name or "").lower() in {"button", "a"} or (e.get_attribute("role") == "tab") else 1)
    for el in targets:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
            el,
        )
        interruptible_sleep(2.4)
        return


def _parse_news_blocks(text: str) -> list[dict[str, str]]:
    """Parse Latest News section from overlay text (fallback)."""
    if "Latest News" not in text:
        return []
    section = text.split("Latest News", 1)[1]
    items: list[dict[str, str]] = []
    # Dates vary: "9/4, 2:14 PM | …", "6:35 AM | …", "Monday 4:34 PM | …"
    chunks = re.split(
        r"\n(?=(?:(?:\d{1,2}/\d{1,2},?\s*)?(?:\w+day\s+)?\d{1,2}:\d{2}\s*[AP]M)\s*\|)",
        section.strip(),
        flags=re.I,
    )
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or len(chunk) < 20:
            continue
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        header = lines[0]
        source = ""
        published = ""
        if "|" in header:
            published, source = [p.strip() for p in header.split("|", 1)]
            title = lines[1] if len(lines) > 1 else ""
            body = "\n".join(lines[2:]) if len(lines) > 2 else ""
        else:
            title = header
            body = "\n".join(lines[1:])
        if not title and not body:
            continue
        items.append(
            {
                "published_at": published,
                "source": source,
                "title": title,
                "body": body,
            }
        )
    return items


def _parse_news_from_dom(driver: WebDriver) -> list[dict[str, str]]:
    """Parse all Latest News cards from the playernote DOM (preferred).

    Selenium element.text often truncates overflow; query the full <li> cards instead.
    """
    rows = driver.execute_script(
        """
        const notes = document.querySelector('.player-notes');
        if (!notes) return [];
        return [...notes.querySelectorAll('ul > li')].map((li) => {
          const spans = [...li.querySelectorAll('header span')]
            .map((s) => (s.textContent || '').trim())
            .filter(Boolean);
          const pipe = spans.findIndex((s) => s === '|');
          let published = '';
          let source = '';
          if (pipe > 0) {
            published = spans.slice(0, pipe).join(' ').trim();
            source = spans.slice(pipe + 1).join(' ').trim();
          } else if (spans.length) {
            published = spans[0];
            source = spans.slice(1).join(' ').replace(/^\\|\\s*/, '');
          }
          const title = (li.querySelector('h4')?.textContent || '').trim();
          const bodyEl =
            li.querySelector('[class*=\"Whs\"]') ||
            li.querySelector('header + div') ||
            li.querySelector('section > div');
          const body = (bodyEl?.innerText || bodyEl?.textContent || '').trim();
          return { published_at: published, source, title, body };
        }).filter((x) => x.title || x.body);
        """
    )
    out: list[dict[str, str]] = []
    for r in rows or []:
        out.append(
            {
                "published_at": str(r.get("published_at") or "").strip(),
                "source": str(r.get("source") or "").strip(),
                "title": str(r.get("title") or "").strip(),
                "body": str(r.get("body") or "").strip(),
            }
        )
    return out


def _header_index(headers: list[str], *names: str) -> int | None:
    lowered = [h.lower().strip() for h in headers]
    # exact match first
    for name in names:
        n = name.lower().strip()
        for i, h in enumerate(lowered):
            if h == n:
                return i
    # then startswith / contained for longer labels only
    for name in names:
        n = name.lower().strip()
        for i, h in enumerate(lowered):
            if len(n) >= 3 and (h.startswith(n) or n.startswith(h) and len(h) >= 3):
                return i
            if len(n) >= 4 and n in h:
                return i
    return None


def _find_stat_tables(overlay) -> list[Any]:
    """Find tables that have a Date or Season column header."""
    tables = []
    seen = set()
    for th in overlay.find_elements(By.CSS_SELECTOR, "th"):
        label = (th.text or "").strip().lower()
        if label not in {"date", "season", "opp"}:
            continue
        try:
            table = th.find_element(By.XPATH, "./ancestor::table[1]")
        except Exception:
            continue
        key = table.id if getattr(table, "id", None) else id(table)
        # use element id from selenium
        eid = table._id  # type: ignore[attr-defined]
        if eid in seen:
            continue
        seen.add(eid)
        tables.append(table)
    return tables


def _table_headers(table) -> list[str]:
    # Prefer the header row that contains Date / Season / Opp
    for tr in table.find_elements(By.CSS_SELECTOR, "thead tr"):
        headers = [c.text.strip() for c in tr.find_elements(By.CSS_SELECTOR, "th")]
        joined = " ".join(headers).lower()
        if "date" in joined or "opp" in joined or "season" in joined:
            return headers
    ths = table.find_elements(By.CSS_SELECTOR, "thead th")
    if ths:
        return [c.text.strip() for c in ths]
    first = table.find_elements(By.CSS_SELECTOR, "tr")
    if first:
        return [c.text.strip() for c in first[0].find_elements(By.CSS_SELECTOR, "th,td")]
    return []


def _parse_game_log_rows(overlay) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for table in _find_stat_tables(overlay):
        headers = _table_headers(table)
        if _header_index(headers, "Date") is None or _header_index(headers, "Opp") is None:
            continue

        i_date = _header_index(headers, "Date")
        i_opp = _header_index(headers, "Opp")
        assert i_date is not None and i_opp is not None

        for tr in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
            cells = [c.text.strip() for c in tr.find_elements(By.CSS_SELECTOR, "td")]
            if len(cells) <= max(i_date, i_opp):
                continue
            date_raw = cells[i_date]
            opp_raw = cells[i_opp]
            if not date_raw or not opp_raw:
                continue
            if date_raw.lower() == "date":
                continue
            is_home = 0 if opp_raw.startswith("@") else 1
            opponent = opp_raw.lstrip("@").strip()

            def get(*labels: str) -> str:
                j = _header_index(headers, *labels)
                return cells[j] if j is not None and j < len(cells) else ""

            rows_out.append(
                {
                    "game_date": date_raw,
                    "opponent": opponent,
                    "is_home": is_home,
                    "status": get("Status"),
                    "minutes": get("MIN"),
                    "fgm_a": get("FGM / FGA", "FGM/A", "FGM"),
                    "fg_pct": get("FG%"),
                    "ftm_a": get("FTM / FTA", "FTM/A", "FTM"),
                    "ft_pct": get("FT%"),
                    "threes": get("3PTM"),
                    "pts": get("PTS"),
                    "reb": get("REB"),
                    "ast": get("AST"),
                    "stl": get("ST"),
                    "blk": get("BLK"),
                    "tov": get("TO"),
                }
            )
    return rows_out


def _nearest_table_heading(table) -> str:
    """Find caption / nearby heading used to label Season Stats tables."""
    try:
        caption = table.find_elements(By.CSS_SELECTOR, "caption")
        if caption and (caption[0].text or "").strip():
            return caption[0].text.strip()
    except Exception:
        pass
    try:
        el = table
        for _ in range(6):
            el = el.find_element(By.XPATH, "./preceding-sibling::*[1]")
            txt = (el.text or "").strip().splitlines()
            if not txt:
                continue
            head = txt[0].strip()
            if head:
                return head
    except Exception:
        pass
    try:
        parent = table.find_element(By.XPATH, "./..")
        for prev in parent.find_elements(By.XPATH, "./*"):
            if prev == table:
                break
            t = (prev.text or "").strip().splitlines()
            if t and "season" in t[0].lower():
                return t[0].strip()
    except Exception:
        pass
    return ""


def _parse_season_rows(overlay) -> list[dict[str, Any]]:
    """Parse Season Stats (totals) + Season Stats (average) for all years shown."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    tables = _find_stat_tables(overlay) or overlay.find_elements(By.CSS_SELECTOR, "table")
    for table in tables:
        heading = _nearest_table_heading(table).lower()
        # Skip game-log / recent rolling windows
        if "game log" in heading or heading.startswith("recent stats"):
            continue
        if "season stats" not in heading and "season" not in " ".join(
            (th.text or "").lower() for th in table.find_elements(By.CSS_SELECTOR, "th")[:3]
        ):
            # Still allow tables whose first body cell is a year
            pass

        if "recent" in heading and "season stats" not in heading:
            continue

        headers = _table_headers(table)
        body_rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
        if not body_rows:
            trs = table.find_elements(By.CSS_SELECTOR, "tr")
            if len(trs) < 2:
                continue
            if not headers:
                headers = [c.text.strip() for c in trs[0].find_elements(By.CSS_SELECTOR, "th,td")]
            body_rows = trs[1:]

        # Must look like a season table (Season header or year in first column)
        header_join = " ".join(headers).lower()
        year_rows_sample: list[list[str]] = []
        for tr in body_rows[:8]:
            tds = [c.text.strip() for c in tr.find_elements(By.CSS_SELECTOR, "td")]
            if tds and re.fullmatch(r"20\d{2}", tds[0]):
                year_rows_sample.append(tds)
        if "season" not in header_join and not year_rows_sample:
            continue

        is_average = "average" in heading
        is_total_heading = ("season stats" in heading) and not is_average
        # Heuristic from sample PTS: averages have decimals like 27.7; totals are large ints.
        if not is_average and not is_total_heading and year_rows_sample:
            pts_idx = _header_index(headers, "PTS") if headers else None
            decimals = 0
            big_ints = 0
            for cells in year_rows_sample:
                raw_pts = cells[pts_idx] if pts_idx is not None and pts_idx < len(cells) else ""
                if not raw_pts or raw_pts in {"-", "–"}:
                    continue
                if "." in raw_pts:
                    decimals += 1
                else:
                    try:
                        if float(raw_pts.replace(",", "")) >= 80:
                            big_ints += 1
                    except ValueError:
                        pass
            if decimals > big_ints:
                is_average = True
            elif big_ints > decimals:
                is_total_heading = True

        # Totals tables include Games Played; average tables usually don't.
        if not is_average and not is_total_heading:
            if "games played" in header_join or "gp" in header_join.split():
                is_total_heading = True
            elif "season stats" in heading or year_rows_sample:
                # default ambiguous year tables without GP toward averages if Rank-only
                is_average = "games played" not in header_join

        for tr in body_rows:
            cells = [c.text.strip() for c in tr.find_elements(By.CSS_SELECTOR, "td")]
            if not cells:
                continue
            season = cells[0]
            if not re.fullmatch(r"20\d{2}", season):
                continue
            # Sanity: Yahoo season years only
            try:
                year_i = int(season)
            except ValueError:
                continue
            if year_i < 1990 or year_i > 2100:
                continue

            def get(*labels: str, default_idx: int | None = None) -> str:
                j = _header_index(headers, *labels) if headers else None
                if j is not None and j < len(cells):
                    return cells[j]
                if default_idx is not None and default_idx < len(cells):
                    return cells[default_idx]
                return ""

            pts = get("PTS")
            record_type = "season_avg" if is_average else "season_total"
            key = (record_type, season)
            if key in seen:
                continue
            seen.add(key)

            # Totals table: Season, Games Played, Rank, ...
            # Averages table: Season, Rank, FGM..., (no GP)
            gp = get("Games Played", "GP")
            rank = get("Rank", "Season Rank")
            if not gp and record_type == "season_total" and len(cells) > 1:
                gp = cells[1]
            if not rank:
                # avg: cells[1] is rank; total: cells[2] is rank when GP present
                if record_type == "season_avg" and len(cells) > 1:
                    rank = cells[1]
                elif record_type == "season_total" and len(cells) > 2:
                    rank = cells[2]

            out.append(
                {
                    "record_type": record_type,
                    "season": season,
                    "games_played": gp,
                    "season_rank": rank,
                    "fgm_a": get("FGM / FGA", "FGM/A", "FGM"),
                    "fg_pct": get("FG%"),
                    "ftm_a": get("FTM / FTA", "FTM/A", "FTM"),
                    "ft_pct": get("FT%"),
                    "threes": get("3PTM"),
                    "pts": pts,
                    "reb": get("REB"),
                    "ast": get("AST"),
                    "stl": get("ST"),
                    "blk": get("BLK"),
                    "tov": get("TO"),
                }
            )
    return out


def _wait_season_stats_loaded(driver: WebDriver, *, timeout_sec: float = 12.0) -> Any:
    """Wait until Season Stats totals + averages tables are present."""
    deadline = time.time() + timeout_sec
    overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
    while time.time() < deadline:
        try:
            driver.execute_script(
                "var o=arguments[0]; o.scrollTop=o.scrollHeight;",
                overlay,
            )
        except Exception:
            pass
        overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
        text = overlay.text or ""
        if "Season Stats (average)" in text:
            rows = _parse_season_rows(overlay)
            types = {r["record_type"] for r in rows}
            if "season_total" in types and "season_avg" in types:
                return overlay
            if rows and time.time() + 2.5 >= deadline:
                return overlay
        time.sleep(0.4)
        overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
    return driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")


def scrape_player_overlay(driver: WebDriver, yahoo_id: str) -> dict[str, Any]:
    _open_playernote(driver, yahoo_id)
    overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")

    # News: prefer DOM cards (element.text truncates overflow and drops items).
    news = _parse_news_from_dom(driver)
    if not news:
        notes = driver.find_elements(By.CSS_SELECTOR, ".player-notes")
        news_text = notes[0].get_attribute("innerText") if notes else (overlay.text or "")
        # innerText via attribute may be None on some drivers — fall back to JS
        if not news_text:
            news_text = (
                driver.execute_script(
                    "var n=document.querySelector('.player-notes');"
                    "return n ? n.innerText : (document.querySelector('#react-playernote-overlay')||{}).innerText || '';"
                )
                or ""
            )
        news = _parse_news_blocks(news_text)

    # Game log tab
    _click_overlay_tab(driver, "Game Log")
    overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
    game_logs = _parse_game_log_rows(overlay)

    # Season Stats tab: all years totals + averages
    season_rows: list[dict[str, Any]] = []
    _click_overlay_tab(driver, "Season Stats")
    overlay = _wait_season_stats_loaded(driver)
    season_rows = _parse_season_rows(overlay)

    # Fallback: Forecast tab sometimes also has season year rows
    if not season_rows:
        _click_overlay_tab(driver, "Forecast")
        overlay = driver.find_element(By.CSS_SELECTOR, "#react-playernote-overlay")
        time.sleep(1.5)
        season_rows = _parse_season_rows(overlay)

    _close_playernote(driver)
    return {"news": news, "game_logs": game_logs, "season_rows": season_rows}


def scrape_team_depth(
    driver: WebDriver,
    *,
    eteam_value: str,
    position: str,
) -> list[dict[str, Any]]:
    """Players on an NBA team filtered by primary position, ordered by Yahoo rank."""
    url = (
        f"{config.LEAGUE_URL}/players?status=ALL&eteam={quote_plus(eteam_value)}"
        f"&sort=AR&jsenabled=1"
    )
    driver.get(url)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    players = _parse_players_table(driver, limit=80)
    matched = []
    for p in players:
        positions = [x.strip() for x in (p.get("positions") or "").split(",") if x.strip()]
        if position and position not in positions:
            continue
        try:
            rank = int(p.get("rank") or 0) or None
        except ValueError:
            rank = None
        matched.append(
            {
                "name": p["name"],
                "yahoo_id": p.get("player_id"),
                "yahoo_rank": rank,
                "positions": p.get("positions"),
                "nba_team": p.get("nba_team"),
            }
        )
    # Sort: ranked first by rank, then unranked last
    matched.sort(key=lambda x: (x["yahoo_rank"] is None, x["yahoo_rank"] or 10_000, x["name"]))
    for i, row in enumerate(matched, start=1):
        row["depth_rank"] = i
    return matched


def _empty_stats() -> dict[str, int]:
    return {
        "players_upserted": 0,
        "news_inserted": 0,
        "news_skipped": 0,
        "game_logs_inserted": 0,
        "game_logs_skipped": 0,
        "season_inserted": 0,
        "season_skipped": 0,
        "depth_inserted": 0,
        "depth_skipped": 0,
    }


def _normalize_fantasy_owner(raw: str | None) -> str | None:
    """Persist team names (not GP crumbs / bare ids) into fantasy_owner."""
    try:
        from fantasy_ownership import _resolve_owner_label

        return _resolve_owner_label(raw)
    except Exception:
        s = str(raw or "").strip()
        return s or None


def ingest_player_from_raw(
    driver: WebDriver,
    raw: dict[str, Any],
    *,
    team_map: dict[str, str] | None = None,
    scrape_details: bool = True,
    conn=None,
    scraped_at: str | None = None,
) -> dict[str, Any]:
    """Upsert one player (+ optional news/gamelog/depth). Skips duplicate stat rows."""
    from db import get_player_by_yahoo_id

    own_conn = conn is None
    if own_conn:
        conn = connect()
    scraped_at = scraped_at or utc_now_iso()
    stats = _empty_stats()

    yahoo_id = str(raw.get("player_id") or raw.get("yahoo_id") or "")
    if not yahoo_id:
        if own_conn:
            conn.close()
        raise ValueError("Missing yahoo player id")

    existing = get_player_by_yahoo_id(yahoo_id, conn=conn)
    already = existing is not None

    full_name = raw.get("name") or raw.get("full_name") or ""
    first, last = split_name(full_name)
    # yahoo_rank = Yahoo players "Current" ranking (never Pre-Season).
    try:
        yrank = int(
            raw.get("rank")
            or raw.get("current_rank")
            or raw.get("yahoo_rank")
            or 0
        ) or None
    except ValueError:
        yrank = None

    player = {
        "yahoo_id": yahoo_id,
        "first_name": first or (existing or {}).get("first_name") or "?",
        "last_name": last or (existing or {}).get("last_name") or "",
        "full_name": full_name or (existing or {}).get("full_name") or yahoo_id,
        "positions": raw.get("positions") or (existing or {}).get("positions"),
        "nba_team": raw.get("nba_team") or (existing or {}).get("nba_team"),
        "profile_url": raw.get("profile_url") or (existing or {}).get("profile_url"),
        "yahoo_rank": yrank if yrank is not None else (existing or {}).get("yahoo_rank"),
        "owned_pct": raw.get("owned_pct") or (existing or {}).get("owned_pct"),
        "fantasy_owner": _normalize_fantasy_owner(
            raw.get("status") or raw.get("fantasy_owner")
        )
        or (existing or {}).get("fantasy_owner"),
    }
    player_id = upsert_player(conn, player, scraped_at=scraped_at)
    stats["players_upserted"] += 1

    # Season averages from search/list row when present
    if raw.get("pts") or raw.get("gp") or raw.get("reb"):
        season_from_list = {
            "record_type": "season_avg",
            "season": "2025",
            "games_played": raw.get("gp"),
            "season_rank": raw.get("rank") or raw.get("yahoo_rank"),
            "fg_pct": raw.get("fg_pct"),
            "ft_pct": raw.get("ft_pct"),
            "threes": raw.get("threes"),
            "pts": raw.get("pts"),
            "reb": raw.get("reb"),
            "ast": raw.get("ast"),
            "stl": raw.get("stl"),
            "blk": raw.get("blk"),
            "tov": raw.get("to"),
        }
        h = content_hash(
            player_id,
            "season_avg",
            "2025",
            season_from_list.get("games_played"),
            season_from_list.get("pts"),
            season_from_list.get("reb"),
            season_from_list.get("ast"),
        )
        ok = insert_stat_if_new(
            conn,
            {
                "player_id": player_id,
                **season_from_list,
                "content_hash": h,
                "scraped_at": scraped_at,
            },
        )
        stats["season_inserted" if ok else "season_skipped"] += 1

    if scrape_details:
        detail: dict[str, Any] = {"news": [], "game_logs": [], "season_rows": []}
        # Prefer name search — works for rostered + FA players.
        try:
            raise_if_cancelled()
            restore_page_load_timeout(driver)
            if full_name:
                driver.get(
                    f"{config.LEAGUE_URL}/playersearch?search={quote_plus(full_name)}"
                )
            else:
                driver.get(f"{config.LEAGUE_URL}/players?status=A&sort=AR")
            interruptible_sleep(config.PAGE_LOAD_WAIT_SEC)
            raise_if_cancelled()
            detail = scrape_player_overlay(driver, yahoo_id)
        except JobCancelled:
            raise
        except Exception as exc:
            raise_if_cancelled()
            restore_page_load_timeout(driver)
            print(f"overlay failed for {full_name or yahoo_id}: {exc}", flush=True)
            if full_name:
                try:
                    raise_if_cancelled()
                    restore_page_load_timeout(driver)
                    driver.get(f"{config.LEAGUE_URL}/players?status=A&sort=AR")
                    interruptible_sleep(config.PAGE_LOAD_WAIT_SEC)
                    raise_if_cancelled()
                    detail = scrape_player_overlay(driver, yahoo_id)
                except JobCancelled:
                    raise
                except Exception as exc2:
                    raise_if_cancelled()
                    restore_page_load_timeout(driver)
                    print(f"overlay fallback failed for {full_name}: {exc2}", flush=True)

        for n in detail["news"]:
            h = content_hash(
                player_id,
                "news",
                n.get("published_at"),
                n.get("source"),
                n.get("title"),
                n.get("body"),
            )
            ok = insert_stat_if_new(
                conn,
                {
                    "player_id": player_id,
                    "record_type": "news",
                    "published_at": n.get("published_at"),
                    "source": n.get("source"),
                    "title": n.get("title"),
                    "body": n.get("body"),
                    "content_hash": h,
                    "scraped_at": scraped_at,
                },
            )
            stats["news_inserted" if ok else "news_skipped"] += 1

        for g in detail["game_logs"]:
            h = content_hash(
                player_id,
                "game_log",
                g.get("game_date"),
                g.get("opponent"),
                g.get("minutes"),
                g.get("pts"),
                g.get("reb"),
                g.get("ast"),
            )
            ok = insert_stat_if_new(
                conn,
                {
                    "player_id": player_id,
                    "record_type": "game_log",
                    **{
                        k: g.get(k)
                        for k in (
                            "game_date",
                            "opponent",
                            "is_home",
                            "status",
                            "minutes",
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
                            "tov",
                        )
                    },
                    "content_hash": h,
                    "scraped_at": scraped_at,
                },
            )
            stats["game_logs_inserted" if ok else "game_logs_skipped"] += 1

        for srow in detail["season_rows"]:
            h = content_hash(
                player_id,
                srow.get("record_type") or "season_total",
                srow.get("season"),
                srow.get("games_played"),
                srow.get("pts"),
                srow.get("reb"),
                srow.get("ast"),
            )
            ok = insert_stat_if_new(
                conn,
                {
                    "player_id": player_id,
                    "record_type": srow.get("record_type") or "season_total",
                    **{
                        k: srow.get(k)
                        for k in (
                            "season",
                            "games_played",
                            "season_rank",
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
                            "tov",
                        )
                    },
                    "content_hash": h,
                    "scraped_at": scraped_at,
                },
            )
            stats["season_inserted" if ok else "season_skipped"] += 1

        team = player.get("nba_team") or ""
        pos = primary_position(player.get("positions"))
        try:
            raise_if_cancelled()
            if team_map is None:
                team_map = _nba_team_to_eteam(driver)
            eteam = team_map.get(team)
            if eteam and pos:
                depth_rows = scrape_team_depth(driver, eteam_value=eteam, position=pos)
                for drow in depth_rows:
                    raise_if_cancelled()
                    h = content_hash(
                        player_id,
                        team,
                        pos,
                        drow["depth_rank"],
                        drow["name"],
                        drow.get("yahoo_id"),
                    )
                    ok = insert_depth_if_new(
                        conn,
                        {
                            "player_id": player_id,
                            "nba_team": team,
                            "position": pos,
                            "depth_rank": drow["depth_rank"],
                            "depth_player_name": drow["name"],
                            "depth_yahoo_id": drow.get("yahoo_id"),
                            "yahoo_rank": drow.get("yahoo_rank"),
                            "content_hash": h,
                            "scraped_at": scraped_at,
                        },
                    )
                    stats["depth_inserted" if ok else "depth_skipped"] += 1
        except JobCancelled:
            raise
        except Exception as exc:
            print(f"depth scrape failed for {player.get('full_name')}: {exc}", flush=True)

    conn.commit()
    result = {
        "ok": True,
        "already_existed": already,
        "player_id": player_id,
        "yahoo_id": yahoo_id,
        "full_name": player["full_name"],
        "stats": stats,
        "scraped_at": scraped_at,
    }
    if own_conn:
        conn.close()
    print(f"OK {player['full_name']} ({player.get('nba_team')} {primary_position(player.get('positions'))})", flush=True)
    return result


def add_player_to_db(raw: dict[str, Any], *, scrape_details: bool = True) -> dict[str, Any]:
    """Attach Chrome and ingest one player. No-op details if already fully present — still upserts."""
    from chrome_browser import attach_chrome
    from db import get_player_by_yahoo_id

    yahoo_id = str(raw.get("player_id") or raw.get("yahoo_id") or "")
    existing = get_player_by_yahoo_id(yahoo_id) if yahoo_id else None
    if existing and not scrape_details:
        return {
            "ok": True,
            "already_existed": True,
            "player_id": existing["id"],
            "yahoo_id": yahoo_id,
            "full_name": existing["full_name"],
            "stats": _empty_stats(),
        }

    driver = attach_chrome()
    team_map = _nba_team_to_eteam(driver)
    return ingest_player_from_raw(
        driver,
        raw,
        team_map=team_map,
        scrape_details=scrape_details,
    )


def _player_row_to_raw(existing: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_id": existing["yahoo_id"],
        "yahoo_id": existing["yahoo_id"],
        "name": existing["full_name"],
        "full_name": existing["full_name"],
        "positions": existing.get("positions"),
        "nba_team": existing.get("nba_team"),
        "profile_url": existing.get("profile_url"),
        "rank": existing.get("yahoo_rank"),
        "yahoo_rank": existing.get("yahoo_rank"),
        "owned_pct": existing.get("owned_pct"),
    }


def refresh_player_in_db(player_id: int) -> dict[str, Any]:
    """Re-scrape Yahoo for an existing DB player (identity + news/gamelog/season/depth)."""
    from chrome_browser import attach_chrome
    from db import get_player_by_id

    existing = get_player_by_id(player_id)
    if not existing:
        return {"ok": False, "error": "Player not found."}

    raw = _player_row_to_raw(existing)
    print(f"Refreshing {existing['full_name']}…", flush=True)
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass
    team_map = _nba_team_to_eteam(driver)
    result = ingest_player_from_raw(
        driver,
        raw,
        team_map=team_map,
        scrape_details=True,
    )
    # Also refresh fantasy Owner column for this player.
    try:
        from fantasy_ownership import update_owner_for_player

        own = update_owner_for_player(
            driver,
            yahoo_id=str(existing.get("yahoo_id") or ""),
            name=str(existing.get("full_name") or ""),
        )
        result["fantasy_owner"] = own.get("fantasy_owner")
    except Exception as exc:
        result["fantasy_owner_error"] = str(exc)
    result["refreshed"] = True
    return result


def refresh_all_players_in_db(
    player_ids: list[int] | None = None,
    progress_cb=None,
) -> dict[str, Any]:
    """Re-scrape full details for all DB players, or only the given ids."""
    import time

    from chrome_browser import attach_chrome

    def _fmt_sec(sec: float | None) -> str:
        if sec is None:
            return "—"
        s = max(0, int(round(float(sec))))
        return f"{s // 60}:{s % 60:02d}"

    selected = bool(player_ids)
    steps: list[dict[str, Any]] = [
        {"key": "chrome", "label": "Attach Chrome", "status": "pending", "detail": ""},
    ]
    if not selected:
        steps.append(
            {
                "key": "owners",
                "label": "Owners + Current ranks (Yahoo ALL)",
                "status": "pending",
                "detail": "",
            }
        )
    steps.extend(
        [
            {
                "key": "team_map",
                "label": "NBA team map",
                "status": "pending",
                "detail": "",
            },
            {
                "key": "players",
                "label": "Player details",
                "status": "pending",
                "detail": "news · gamelog · season · depth · owner",
            },
        ]
    )
    step_started: dict[str, float] = {}

    def _emit(
        current: int,
        total: int,
        message: str,
        *,
        step: str | None = None,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        if not progress_cb:
            return
        if step is not None:
            for s in steps:
                if s["key"] != step:
                    continue
                if status:
                    s["status"] = status
                if detail is not None:
                    s["detail"] = detail
                started = step_started.get(step)
                if started is not None and s["status"] in {"running", "done"}:
                    s["elapsed_sec"] = round(time.time() - started, 1)
                break
        progress_cb(
            current,
            total,
            message,
            steps=steps,
            step=step,
            status=status,
            detail=detail,
        )

    def _begin(key: str, message: str) -> None:
        for s in steps:
            if s["status"] == "running":
                s["status"] = "done"
                started = step_started.get(s["key"])
                if started is not None:
                    s["elapsed_sec"] = round(time.time() - started, 1)
            if s["key"] == key:
                s["status"] = "running"
                s["detail"] = ""
                s["elapsed_sec"] = None
        step_started[key] = time.time()
        _emit(0, max(len(steps), 1), message, step=key, status="running")

    def _done(key: str, detail: str = "") -> None:
        for s in steps:
            if s["key"] == key:
                s["status"] = "done"
                if detail:
                    s["detail"] = detail
                started = step_started.get(key)
                if started is not None:
                    s["elapsed_sec"] = round(time.time() - started, 1)
                break
        _emit(0, max(len(steps), 1), detail or "Done", step=key, status="done", detail=detail)

    conn = connect()
    try:
        if player_ids:
            ids = [int(x) for x in player_ids]
            placeholders = ",".join("?" for _ in ids)
            rows = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT id, yahoo_id, full_name, positions, nba_team,
                           profile_url, yahoo_rank, owned_pct
                    FROM players
                    WHERE id IN ({placeholders})
                    ORDER BY
                        CASE WHEN yahoo_rank IS NULL THEN 1 ELSE 0 END,
                        yahoo_rank ASC,
                        full_name ASC
                    """,
                    ids,
                ).fetchall()
            ]
        else:
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, yahoo_id, full_name, positions, nba_team,
                           profile_url, yahoo_rank, owned_pct
                    FROM players
                    ORDER BY
                        CASE WHEN yahoo_rank IS NULL THEN 1 ELSE 0 END,
                        yahoo_rank ASC,
                        full_name ASC
                    """
                ).fetchall()
            ]
    finally:
        conn.close()

    if not rows:
        if progress_cb:
            progress_cb(0, 0, "No players to refresh.", steps=steps)
        return {
            "ok": True,
            "total": 0,
            "refreshed": 0,
            "failed_count": 0,
            "failed": [],
            "stats": _empty_stats(),
        }

    n_players = len(rows)
    label = "selected" if selected else "all"
    print(f"Refreshing {label} {n_players} players in DB…", flush=True)

    _begin("chrome", f"Attaching Chrome · {n_players} players queued…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass
    _done("chrome", "Chrome attached")

    # Owners: full DB refresh → one ALL scrape; selected → per player.
    ownership_sync: dict[str, Any] = {}
    if not selected:
        _begin("owners", "Updating owners + Current ranks from Yahoo (status=ALL)…")
        try:
            from fantasy_ownership import sync_ownership_from_yahoo_list

            ownership_sync = sync_ownership_from_yahoo_list(driver, limit=900)
            detail = (
                f"{ownership_sync.get('owners') or 0} owners · "
                f"{ownership_sync.get('ranks') or 0} Current ranks · "
                f"{ownership_sync.get('mine') or 0} yours"
            )
            print(
                f"Ownership + Current ranks: {ownership_sync.get('owners')} owners, "
                f"{ownership_sync.get('ranks')} ranks "
                f"({ownership_sync.get('mine')} yours)",
                flush=True,
            )
            _done("owners", detail)
        except Exception as exc:
            ownership_sync = {"ok": False, "error": str(exc)}
            print(f"Ownership sync failed: {exc}", flush=True)
            for s in steps:
                if s["key"] == "owners":
                    s["status"] = "error"
                    s["detail"] = str(exc)
            _emit(0, n_players, f"Owner sync failed: {exc}", step="owners", status="error")

    _begin("team_map", "Building NBA team → Yahoo eteam map…")
    team_map = _nba_team_to_eteam(driver)
    _done("team_map", f"{len(team_map)} teams mapped")

    scraped_at = utc_now_iso()
    totals = _empty_stats()
    refreshed = 0
    failed: list[dict[str, Any]] = []
    owners_updated = 0
    players_t0 = time.time()

    _begin(
        "players",
        f"Refreshing player pages · 0/{n_players} · news · gamelog · season · depth…",
    )

    conn = connect()
    try:
        for i, existing in enumerate(rows, start=1):
            name = existing.get("full_name") or existing.get("yahoo_id")
            print(f"[{i}/{n_players}] {name}", flush=True)
            elapsed = time.time() - players_t0
            per = elapsed / max(i - 1, 1) if i > 1 else None
            eta = (per * (n_players - i + 1)) if per else None
            eta_bit = f" · ETA ~{_fmt_sec(eta)}" if eta is not None else ""
            rate_bit = f" · {_fmt_sec(per)}/player" if per else ""
            msg = (
                f"Refreshing {name} · {i}/{n_players}"
                f" · news+gamelog+season+depth{rate_bit}{eta_bit}"
            )
            _emit(
                i - 1,
                n_players,
                msg,
                step="players",
                status="running",
                detail=f"{name} · {i}/{n_players} · ok {refreshed} · fail {len(failed)}{eta_bit}",
            )
            try:
                result = ingest_player_from_raw(
                    driver,
                    _player_row_to_raw(existing),
                    team_map=team_map,
                    scrape_details=True,
                    conn=conn,
                    scraped_at=scraped_at,
                )
                for k, v in (result.get("stats") or {}).items():
                    totals[k] = totals.get(k, 0) + v
                if selected:
                    try:
                        from fantasy_ownership import update_owner_for_player

                        own = update_owner_for_player(
                            driver,
                            yahoo_id=str(existing.get("yahoo_id") or ""),
                            name=str(name or ""),
                        )
                        if own.get("fantasy_owner"):
                            owners_updated += 1
                    except Exception as own_exc:
                        print(f"  owner skip {name}: {own_exc}", flush=True)
                refreshed += 1
            except Exception as exc:
                print(f"  FAILED {name}: {exc}", flush=True)
                failed.append(
                    {
                        "player_id": existing.get("id"),
                        "yahoo_id": existing.get("yahoo_id"),
                        "full_name": name,
                        "error": str(exc),
                    }
                )
            if progress_cb:
                elapsed = time.time() - players_t0
                per = elapsed / i
                eta = per * (n_players - i)
                _emit(
                    i,
                    n_players,
                    f"Done {name} · {i}/{n_players} · ETA ~{_fmt_sec(eta)}",
                    step="players",
                    status="running",
                    detail=(
                        f"{i}/{n_players} done · ok {refreshed} · fail {len(failed)} · "
                        f"avg {_fmt_sec(per)}/player"
                    ),
                )
            if i % 5 == 0:
                conn.commit()
        conn.commit()
    finally:
        conn.close()

    players_elapsed = round(time.time() - players_t0, 1)
    _done(
        "players",
        f"{refreshed}/{n_players} refreshed · {len(failed)} failed · {_fmt_sec(players_elapsed)}",
    )
    if progress_cb:
        progress_cb(
            n_players,
            n_players,
            f"Finished · {refreshed} refreshed · {len(failed)} failed · {_fmt_sec(players_elapsed)}",
            steps=steps,
        )

    return {
        "ok": True,
        "total": n_players,
        "refreshed": refreshed,
        "failed_count": len(failed),
        "failed": failed[:20],
        "ownership": ownership_sync
        or {"ok": True, "owners_updated": owners_updated, "mode": "per_player"},
        "scraped_at": scraped_at,
        "elapsed_sec": players_elapsed,
        "stats": totals,
    }


def add_missing_players(
    *,
    limit: int | None = 10,
    all_players: bool = False,
    scrape_details: bool = True,
    progress_cb=None,
) -> dict[str, Any]:
    """Add up to `limit` top-ranked players that are not already in the local DB.

    If all_players=True (or limit is None), walk the full Yahoo ranked list
    (~800+) and add every missing player.

    Always scrapes full details (news, game log, season, depth) unless
    scrape_details=False is passed explicitly.
    """
    from chrome_browser import attach_chrome
    from db import get_yahoo_ids

    # Yahoo fantasy lists ~800–900 players; 1000 is a safe ceiling for "all".
    YAHOO_LIST_CEILING = 1000

    if all_players or limit is None:
        all_players = True
        limit = YAHOO_LIST_CEILING
    else:
        limit = max(1, min(int(limit), YAHOO_LIST_CEILING))

    scrape_details = bool(scrape_details)
    label = "all missing" if all_players else str(limit)
    print(
        f"Attaching to Chrome for {label} new players "
        f"(details={'on' if scrape_details else 'off'})…",
        flush=True,
    )
    if progress_cb:
        progress_cb(0, 1, "Attaching Chrome…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass

    known = get_yahoo_ids()
    scraped_at = utc_now_iso()
    totals = _empty_stats()
    added: list[dict[str, Any]] = []
    skipped_existing = 0

    # Over-fetch so we can skip ones already in DB. For "all", pull the full list.
    if all_players:
        fetch_n = YAHOO_LIST_CEILING
    else:
        fetch_n = min(YAHOO_LIST_CEILING, max(limit + len(known) + 50, limit + 50))
    print(f"Loading NBA team map + top {fetch_n} ranked players…", flush=True)
    if progress_cb:
        progress_cb(0, 1, f"Loading Yahoo ranked list ({fetch_n})…")
    team_map = _nba_team_to_eteam(driver) if scrape_details else {}
    pool = ranked_players(driver, limit=fetch_n, status="ALL", sort="AR")

    targets: list[dict[str, Any]] = []
    for raw in pool:
        pid = str(raw.get("player_id") or "")
        if not pid:
            continue
        if pid in known:
            skipped_existing += 1
            continue
        targets.append(raw)
        if not all_players and len(targets) >= limit:
            break

    print(
        f"Yahoo pool {len(pool)} · will add {len(targets)} new "
        f"(skipped {skipped_existing} already in DB)",
        flush=True,
    )
    if progress_cb:
        progress_cb(0, max(len(targets), 1), f"Adding {len(targets)} new players…")

    conn = connect()
    try:
        total_t = max(len(targets), 1)
        for i, raw in enumerate(targets, start=1):
            name = raw.get("name") or raw.get("player_id")
            print(f"[{i}/{len(targets)}] {name}", flush=True)
            if progress_cb:
                progress_cb(i - 1, total_t, f"Adding {name} ({i}/{len(targets)})")
            result = ingest_player_from_raw(
                driver,
                raw,
                team_map=team_map,
                scrape_details=scrape_details,
                conn=conn,
                scraped_at=scraped_at,
            )
            for k, v in (result.get("stats") or {}).items():
                totals[k] = totals.get(k, 0) + v
            added.append(
                {
                    "player_id": result.get("player_id"),
                    "yahoo_id": result.get("yahoo_id"),
                    "full_name": result.get("full_name"),
                }
            )
            known.add(str(result.get("yahoo_id") or ""))
            if progress_cb:
                progress_cb(i, total_t, f"Added {name} ({i}/{len(targets)})")
            if i % 25 == 0:
                conn.commit()
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "limit": None if all_players else limit,
        "all_players": all_players,
        "requested": None if all_players else limit,
        "pool_size": len(pool),
        "added_count": len(added),
        "skipped_existing": skipped_existing,
        "scrape_details": scrape_details,
        "added": added,
        "scraped_at": scraped_at,
        "stats": totals,
    }


def sync_player_database(*, limit: int = 10) -> dict[str, Any]:
    from chrome_browser import attach_chrome

    print("Attaching to Chrome…", flush=True)
    driver = attach_chrome()
    scraped_at = utc_now_iso()
    totals = _empty_stats()

    print("Loading NBA team map…", flush=True)
    team_map = _nba_team_to_eteam(driver)
    print(f"Fetching top {limit} available players…", flush=True)
    top = top_available(driver, limit=limit)
    targets = top.get("players") or []
    print(f"Found {len(targets)} players", flush=True)

    conn = connect()
    try:
        for raw in targets:
            result = ingest_player_from_raw(
                driver,
                raw,
                team_map=team_map,
                scrape_details=True,
                conn=conn,
                scraped_at=scraped_at,
            )
            for k, v in (result.get("stats") or {}).items():
                totals[k] = totals.get(k, 0) + v
        conn.commit()
    finally:
        conn.close()

    return {"ok": True, "limit": limit, "scraped_at": scraped_at, "stats": totals}
