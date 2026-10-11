"""Scrape CBS Most Viewed + Yahoo Transaction Trends."""

from __future__ import annotations

import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from db import connect, replace_roster_trends
from store import utc_now_iso

CBS_VIEWED_URL = "https://www.cbssports.com/fantasy/basketball/trends/viewed/all/"
YAHOO_RESEARCH_URL = f"{config.LEAGUE_URL}/research"
YAHOO_TRENDS_URL = f"{config.LEAGUE_URL}/buzzindex"

SOURCE_CBS = "cbs_viewed"
SOURCE_YAHOO = "yahoo_transactions"
CBS_LABEL = "CBS Sports · Most Viewed"
YAHOO_LABEL = "Yahoo Fantasy · Transaction Trends"
TOP_N = 20


def _dismiss_cbs_overlays(driver: WebDriver) -> None:
    try:
        driver.execute_script(
            """
            const btn = document.querySelector('#onetrust-accept-btn-handler');
            if (btn) { try { btn.click(); } catch (e) {} }
            document.querySelectorAll(
              '#onetrust-banner-sdk,#onetrust-consent-sdk,[id*="onetrust"]'
            ).forEach((e) => e.remove());
            """
        )
    except Exception:
        pass


def scrape_cbs_most_viewed(driver: WebDriver, *, limit: int = TOP_N) -> list[dict[str, Any]]:
    driver.get(CBS_VIEWED_URL)
    wait = WebDriverWait(driver, 25)
    time.sleep(2.0)
    _dismiss_cbs_overlays(driver)
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "table.TableBase-table tbody tr")
            )
        )
    except Exception:
        _dismiss_cbs_overlays(driver)
        time.sleep(2.0)
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "table.TableBase-table tbody tr")
            )
        )
    time.sleep(0.8)
    _dismiss_cbs_overlays(driver)
    rows = driver.execute_script(
        """
        const limit = arguments[0];
        const trs = [...document.querySelectorAll('table.TableBase-table tbody tr')].slice(0, limit);
        return trs.map((tr, idx) => {
          const tds = [...tr.querySelectorAll('td')];
          const long = tr.querySelector('.CellPlayerName--long a');
          const short = tr.querySelector('.CellPlayerName--short a');
          const nameEl = long || short;
          const pos = (tr.querySelector('.CellPlayerName--long .CellPlayerName-position')
            || tr.querySelector('.CellPlayerName-position'));
          const team = (tr.querySelector('.CellPlayerName--long .CellPlayerName-team')
            || tr.querySelector('.CellPlayerName-team'));
          const href = nameEl ? (nameEl.getAttribute('href') || '') : '';
          const m = href.match(/\\/nba\\/players\\/(\\d+)\\//);
          return {
            rank: idx + 1,
            player_name: nameEl ? (nameEl.textContent || '').trim() : '',
            positions: pos ? (pos.textContent || '').trim() : '',
            nba_team: team ? (team.textContent || '').trim() : '',
            recent_views: tds[1] ? (tds[1].textContent || '').trim() : '',
            today_views: tds[2] ? (tds[2].textContent || '').trim() : '',
            external_id: m ? m[1] : '',
          };
        }).filter((x) => x.player_name);
        """,
        int(limit),
    )
    return list(rows or [])


def scrape_yahoo_transaction_trends(
    driver: WebDriver, *, limit: int = TOP_N
) -> list[dict[str, Any]]:
    """Full Transaction Trends table (Research → View More → buzzindex)."""
    driver.get(YAHOO_TRENDS_URL)
    wait = WebDriverWait(driver, 25)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
    time.sleep(max(1.5, float(getattr(config, "PAGE_LOAD_WAIT_SEC", 4)) * 0.5))

    # Prefer Available Players view (matches Research preview).
    try:
        driver.execute_script(
            """
            const labels = [...document.querySelectorAll('label, a, span, button')];
            for (const el of labels) {
              const t = (el.textContent || '').trim();
              if (/^Available Players/i.test(t)) {
                el.click();
                return true;
              }
            }
            const selects = [...document.querySelectorAll('select')];
            for (const s of selects) {
              const opts = [...s.options];
              const hit = opts.find((o) => /available/i.test(o.textContent || ''));
              if (hit) { s.value = hit.value; s.dispatchEvent(new Event('change', {bubbles:true})); return true; }
            }
            return false;
            """
        )
        time.sleep(1.2)
    except Exception:
        pass

    rows = driver.execute_script(
        """
        const limit = arguments[0];
        const table = [...document.querySelectorAll('table')].find((t) => {
          const heads = [...t.querySelectorAll('th')].map((th) => (th.textContent || '').trim());
          return heads.includes('% Ros') && heads.some((h) => /Adds/i.test(h));
        }) || document.querySelector('table.Tst-table') || document.querySelector('table');
        if (!table) return [];
        const trs = [...table.querySelectorAll('tbody tr')].slice(0, limit);
        return trs.map((tr, idx) => {
          const nameEl = tr.querySelector('a.name, .ysf-player-name a');
          const metaEl = [...tr.querySelectorAll('.Fz-xxs, .ysf-player-name span')].find((el) => {
            const t = (el.textContent || '').trim();
            return t.includes(' - ') && t.length < 40;
          });
          const meta = metaEl ? (metaEl.textContent || '').trim() : '';
          let team = '', positions = '';
          if (meta.includes(' - ')) {
            const parts = meta.split(' - ');
            team = (parts[0] || '').trim();
            positions = parts.slice(1).join(' - ').trim();
          }
          const tds = [...tr.querySelectorAll('td')];
          // Layout: watch | player | note | status | %Ros | %Start | Adds | Drops | Trades | Total | chart
          const texts = tds.map((td) => (td.textContent || '').trim());
          let statusIdx = texts.findIndex((t) => t === 'FA' || t === 'W' || t.startsWith('W ('));
          if (statusIdx < 0) statusIdx = 3;
          const status = texts[statusIdx] || '';
          const owned = texts[statusIdx + 1] || '';
          const start = texts[statusIdx + 2] || '';
          const adds = texts[statusIdx + 3] || '';
          const drops = texts[statusIdx + 4] || '';
          const trades = texts[statusIdx + 5] || '';
          const total = texts[statusIdx + 6] || '';
          const watch = tr.querySelector('a[name^="w-"]');
          const watchName = watch ? (watch.getAttribute('name') || '') : '';
          const wm = watchName.match(/w-\\d+-(\\d+)/);
          const href = nameEl ? (nameEl.getAttribute('href') || '') : '';
          const hm = href.match(/\\/players\\/(\\d+)/);
          return {
            rank: idx + 1,
            player_name: nameEl ? (nameEl.textContent || '').trim() : '',
            positions,
            nba_team: team,
            roster_status: status,
            owned_pct: owned,
            start_pct: start,
            adds,
            drops,
            trades,
            total,
            external_id: (wm && wm[1]) || (hm && hm[1]) || '',
          };
        }).filter((x) => x.player_name);
        """,
        int(limit),
    )
    return list(rows or [])


def sync_trends(*, progress_cb=None) -> dict[str, Any]:
    from chrome_browser import attach_chrome

    if progress_cb:
        progress_cb(0, 3, "Attaching Chrome…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass

    scraped_at = utc_now_iso()
    by_source: dict[str, Any] = {}

    if progress_cb:
        progress_cb(0, 3, "Loading CBS Most Viewed…")
    print(f"Loading {CBS_VIEWED_URL}…", flush=True)
    cbs_rows = scrape_cbs_most_viewed(driver, limit=TOP_N)
    print(f"CBS Most Viewed: {len(cbs_rows)} players", flush=True)

    if progress_cb:
        progress_cb(1, 3, "Loading Yahoo Transaction Trends…")
    print(f"Loading {YAHOO_TRENDS_URL}…", flush=True)
    yahoo_rows = scrape_yahoo_transaction_trends(driver, limit=TOP_N)
    print(f"Yahoo Transaction Trends: {len(yahoo_rows)} players", flush=True)

    conn = connect()
    try:
        if progress_cb:
            progress_cb(2, 3, "Saving trends…")
        cbs_result = replace_roster_trends(
            conn,
            source=SOURCE_CBS,
            source_label=CBS_LABEL,
            source_url=CBS_VIEWED_URL,
            rows=cbs_rows,
            scraped_at=scraped_at,
        )
        yahoo_result = replace_roster_trends(
            conn,
            source=SOURCE_YAHOO,
            source_label=YAHOO_LABEL,
            source_url=YAHOO_RESEARCH_URL,
            rows=yahoo_rows,
            scraped_at=scraped_at,
        )
        conn.commit()
        by_source["cbs"] = {"fetched": len(cbs_rows), **cbs_result}
        by_source["yahoo"] = {"fetched": len(yahoo_rows), **yahoo_result}
        if progress_cb:
            progress_cb(
                3,
                3,
                f"Done — CBS {cbs_result['inserted']}, Yahoo {yahoo_result['inserted']}",
            )
    finally:
        conn.close()

    return {
        "ok": True,
        "scraped_at": scraped_at,
        "fetched": len(cbs_rows) + len(yahoo_rows),
        "inserted": int(cbs_result["inserted"]) + int(yahoo_result["inserted"]),
        "matched": int(cbs_result["matched"]) + int(yahoo_result["matched"]),
        "by_source": by_source,
        "source_urls": {
            "cbs": CBS_VIEWED_URL,
            "yahoo": YAHOO_RESEARCH_URL,
            "yahoo_trends": YAHOO_TRENDS_URL,
        },
    }
