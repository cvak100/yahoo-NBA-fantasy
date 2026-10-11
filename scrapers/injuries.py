"""Scrape CBS Sports NBA injury report into local DB."""

from __future__ import annotations

import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db import connect, replace_injuries
from store import utc_now_iso

INJURIES_URL = "https://www.cbssports.com/nba/injuries/"


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


def _parse_injuries_js(driver: WebDriver) -> list[dict[str, Any]]:
    return driver.execute_script(
        """
        const wrappers = [...document.querySelectorAll('.TableBaseWrapper')];
        const out = [];
        for (const w of wrappers) {
          const titleEl = w.querySelector('h4');
          const team = titleEl ? (titleEl.textContent || '').trim() : '';
          if (!team) continue;
          const rows = [...w.querySelectorAll('table.TableBase-table tbody tr')];
          for (const tr of rows) {
            const tds = [...tr.querySelectorAll('td')];
            if (tds.length < 4) continue;
            const links = [...tr.querySelectorAll('a[href*="/nba/players/"]')];
            let name = '';
            let href = '';
            let cbsId = '';
            for (const a of links) {
              const t = (a.textContent || '').trim();
              if (!t) continue;
              // Prefer full name over abbreviated "K. Gilbert"
              if (!name || t.length > name.length) {
                name = t;
                href = a.getAttribute('href') || '';
              }
            }
            const m = href.match(/\\/nba\\/players\\/(\\d+)\\//);
            if (m) cbsId = m[1];
            out.push({
              team_name: team,
              player_name: name,
              position: tds[1] ? (tds[1].textContent || '').trim() : '',
              updated_at: tds[2] ? (tds[2].textContent || '').trim() : '',
              injury: tds[3] ? (tds[3].textContent || '').trim() : '',
              injury_status: tds[4] ? (tds[4].textContent || '').trim() : '',
              cbs_player_id: cbsId,
            });
          }
        }
        return out.filter((x) => x.player_name && x.team_name);
        """
    )


def scrape_injuries_page(driver: WebDriver) -> list[dict[str, Any]]:
    driver.get(INJURIES_URL)
    wait = WebDriverWait(driver, 25)
    time.sleep(2.0)
    _dismiss_cbs_overlays(driver)
    try:
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".TableBaseWrapper table"))
        )
    except Exception:
        _dismiss_cbs_overlays(driver)
        time.sleep(2.0)
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".TableBaseWrapper table"))
        )
    time.sleep(1.0)
    _dismiss_cbs_overlays(driver)
    return list(_parse_injuries_js(driver) or [])


def sync_injuries(*, progress_cb=None) -> dict[str, Any]:
    from chrome_browser import attach_chrome

    if progress_cb:
        progress_cb(0, 2, "Attaching Chrome…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass

    if progress_cb:
        progress_cb(0, 2, "Loading CBS injury report…")
    print(f"Loading {INJURIES_URL}…", flush=True)
    rows = scrape_injuries_page(driver)
    print(f"Parsed {len(rows)} injury rows", flush=True)

    scraped_at = utc_now_iso()
    conn = connect()
    try:
        if progress_cb:
            progress_cb(1, 2, f"Saving {len(rows)} injuries…")
        result = replace_injuries(conn, rows, scraped_at=scraped_at)
        conn.commit()
        if progress_cb:
            progress_cb(
                2,
                2,
                f"Done — {result['inserted']} players, {result['matched']} linked",
            )
    finally:
        conn.close()

    teams = sorted({(r.get("team_name") or "").strip() for r in rows if r.get("team_name")})
    return {
        "ok": True,
        "scraped_at": scraped_at,
        "fetched": len(rows),
        "team_count": len(teams),
        "source_url": INJURIES_URL,
        **result,
    }
