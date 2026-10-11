"""Scrape NBA.com + CBS Sports fantasy news and sync into local DB + player news."""

from __future__ import annotations

import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from db import (
    attach_fantasy_news_to_player_stats,
    connect,
    content_hash,
    find_player_id_by_name,
    insert_fantasy_news_if_new,
    relink_fantasy_news,
)
from store import utc_now_iso

FANTASY_NEWS_URL = "https://www.nba.com/stats/fantasynews"
CBS_NEWS_BASE = "https://www.cbssports.com/fantasy/basketball/players/news/all"
SOURCE_NBA = "NBA.com Fantasy News"
SOURCE_CBS = "CBS Sports"
CBS_PAGES = 5


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


def _parse_nba_articles_js(driver: WebDriver) -> list[dict[str, Any]]:
    """Extract news cards from the rendered NBA stats page."""
    return driver.execute_script(
        """
        const articles = [...document.querySelectorAll('article[class*="StatsFantasyNewsItem_article"]')];
        return articles.map((a) => {
          const nameEl = a.querySelector('h2 a, [class*="StatsFantasyNewsItem_header"] a');
          const headlineEl = a.querySelector('[class*="StatsFantasyNewsItem_headline"]');
          const timeEl = a.querySelector('time');
          const bodyEl = a.querySelector('[class*="StatsFantasyNewsItem_content"] p, [class*="StatsFantasyNewsItem_content"]');
          const teamImg = a.querySelector('[class*="TeamLogo"] img, img[alt*="Logo"]');
          const href = nameEl ? (nameEl.getAttribute('href') || '') : '';
          const m = href.match(/\\/player\\/(\\d+)/);
          return {
            player_name: nameEl ? (nameEl.textContent || '').trim() : '',
            title: headlineEl ? (headlineEl.textContent || '').trim() : '',
            published_at: timeEl ? (timeEl.textContent || '').trim() : '',
            published_iso: timeEl ? (timeEl.getAttribute('datetime') || '') : '',
            body: bodyEl ? (bodyEl.textContent || '').trim() : '',
            nba_player_id: m ? m[1] : '',
            team_name: teamImg ? ((teamImg.getAttribute('title') || teamImg.getAttribute('alt') || '').replace(/\\s*Logo$/i, '').trim()) : '',
          };
        }).filter((x) => x.player_name || x.body || x.title);
        """
    )


def _parse_cbs_articles_js(driver: WebDriver) -> list[dict[str, Any]]:
    return driver.execute_script(
        """
        const items = [...document.querySelectorAll('ul.player-news-by-sport > li')];
        return items.map((li) => {
          const nameEl = li.querySelector('.players-annotated a');
          const metaEl = li.querySelector('.players-annotated span');
          const timeEl = li.querySelector('time');
          const titleEl = li.querySelector('.player-news-desc h4 a');
          const bodyPs = [...li.querySelectorAll('.latest-updates p')];
          const meta = metaEl ? (metaEl.textContent || '').trim() : '';
          let team = '';
          const parts = meta.split('|').map((s) => s.trim()).filter(Boolean);
          if (parts.length >= 2) team = parts[parts.length - 1];
          const href = nameEl ? (nameEl.getAttribute('href') || '') : '';
          const m = href.match(/\\/nba\\/players\\/(\\d+)\\//);
          return {
            player_name: nameEl ? (nameEl.textContent || '').trim() : '',
            title: titleEl ? (titleEl.textContent || '').trim() : '',
            published_at: timeEl ? (timeEl.textContent || '').trim() : '',
            published_iso: timeEl ? (timeEl.getAttribute('datetime') || '') : '',
            body: bodyPs.map((p) => (p.textContent || '').trim()).filter(Boolean).join('\\n\\n'),
            nba_player_id: m ? m[1] : '',
            team_name: team,
            article_url: titleEl ? (titleEl.getAttribute('href') || '') : '',
          };
        }).filter((x) => x.player_name || x.body || x.title);
        """
    )


def scrape_fantasy_news_page(driver: WebDriver) -> list[dict[str, Any]]:
    driver.get(FANTASY_NEWS_URL)
    wait = WebDriverWait(driver, 25)
    wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, 'article[class*="StatsFantasyNewsItem_article"]')
        )
    )
    time.sleep(max(1.5, float(getattr(config, "PAGE_LOAD_WAIT_SEC", 4)) * 0.5))
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.0)
        driver.execute_script("window.scrollTo(0, 0);")
    except Exception:
        pass
    rows = _parse_nba_articles_js(driver) or []
    return list(rows)


def _cbs_page_url(page: int) -> str:
    if page <= 1:
        return f"{CBS_NEWS_BASE}/"
    return f"{CBS_NEWS_BASE}/{page}/"


def scrape_cbs_fantasy_news_pages(
    driver: WebDriver, *, pages: int = CBS_PAGES, progress_cb=None
) -> list[dict[str, Any]]:
    """Scrape CBS fantasy player news across the first N pages."""
    pages = max(1, min(int(pages), 10))
    wait = WebDriverWait(driver, 25)
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(1, pages + 1):
        url = _cbs_page_url(page)
        if progress_cb:
            progress_cb(page - 1, pages, f"Loading CBS news page {page}/{pages}…")
        print(f"Loading {url}…", flush=True)
        driver.get(url)
        time.sleep(2.0)
        _dismiss_cbs_overlays(driver)
        try:
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.player-news-by-sport > li")
                )
            )
        except Exception:
            _dismiss_cbs_overlays(driver)
            time.sleep(2.0)
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.player-news-by-sport > li")
                )
            )
        time.sleep(1.0)
        _dismiss_cbs_overlays(driver)
        rows = _parse_cbs_articles_js(driver) or []
        print(f"CBS page {page}: {len(rows)} items", flush=True)
        for row in rows:
            key = "|".join(
                [
                    (row.get("player_name") or "").strip(),
                    (row.get("title") or "").strip(),
                    (row.get("published_at") or "").strip(),
                    (row.get("body") or "").strip()[:120],
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)
        if progress_cb:
            progress_cb(page, pages, f"CBS page {page}/{pages}: {len(rows)} items")

    return all_rows


def _persist_articles(
    conn,
    articles: list[dict[str, Any]],
    *,
    source: str,
    scraped_at: str,
    progress_cb=None,
    progress_offset: int = 0,
    progress_total: int | None = None,
) -> dict[str, Any]:
    inserted = 0
    skipped = 0
    matched = 0
    player_news_inserted = 0
    unmatched_names: list[str] = []
    total = progress_total or max(len(articles), 1)

    for i, art in enumerate(articles, start=1):
        name = (art.get("player_name") or "").strip()
        title = (art.get("title") or "").strip()
        body = (art.get("body") or "").strip()
        published_at = (art.get("published_at") or "").strip()
        published_iso = (art.get("published_iso") or "").strip()
        nba_pid = (art.get("nba_player_id") or "").strip() or None
        team = (art.get("team_name") or "").strip() or None

        if progress_cb and (i == 1 or i % 10 == 0 or i == len(articles)):
            progress_cb(
                progress_offset + i - 1,
                total,
                f"Saving {source} ({i}/{len(articles)})…",
            )

        # NBA hashes omit source for backward compatibility with existing rows.
        if source == SOURCE_NBA:
            h = content_hash(
                "fantasy_news",
                published_iso or published_at,
                name,
                title,
                body,
                nba_pid,
            )
        else:
            h = content_hash(
                "fantasy_news",
                source,
                published_iso or published_at,
                name,
                title,
                body,
                nba_pid,
            )
        player_id = find_player_id_by_name(name, conn=conn)
        if player_id:
            matched += 1
        elif name and name not in unmatched_names:
            unmatched_names.append(name)

        row = {
            "content_hash": h,
            "player_name": name or None,
            "player_id": player_id,
            "nba_player_id": nba_pid,
            "published_at": published_at or None,
            "published_iso": published_iso or None,
            "source": source,
            "title": title or None,
            "body": body or None,
            "team_name": team,
            "scraped_at": scraped_at,
        }
        ok = insert_fantasy_news_if_new(conn, row)
        if ok:
            inserted += 1
        else:
            skipped += 1
            if player_id:
                conn.execute(
                    """
                    UPDATE fantasy_news
                    SET player_id = COALESCE(player_id, ?)
                    WHERE content_hash = ?
                    """,
                    (player_id, h),
                )

        if player_id and attach_fantasy_news_to_player_stats(
            conn, row, scraped_at=scraped_at
        ):
            player_news_inserted += 1

        if i % 25 == 0:
            conn.commit()

    return {
        "fetched": len(articles),
        "inserted": inserted,
        "skipped": skipped,
        "matched": matched,
        "player_news_inserted": player_news_inserted,
        "unmatched_sample": unmatched_names[:20],
        "source": source,
    }


def sync_fantasy_news(*, progress_cb=None, include_nba: bool = True, include_cbs: bool = True) -> dict[str, Any]:
    """Fetch fantasy news from NBA and/or CBS, insert new items, attach matched ones."""
    from chrome_browser import attach_chrome

    if not include_nba and not include_cbs:
        include_nba = True
        include_cbs = True

    if progress_cb:
        progress_cb(0, 1, "Attaching Chrome…")
    driver = attach_chrome()
    try:
        driver.set_page_load_timeout(90)
    except Exception:
        pass

    scraped_at = utc_now_iso()
    sources_run: list[str] = []
    totals = {
        "fetched": 0,
        "inserted": 0,
        "skipped": 0,
        "matched": 0,
        "player_news_inserted": 0,
    }
    unmatched_sample: list[str] = []
    by_source: dict[str, Any] = {}

    conn = connect()
    try:
        step = 0
        steps = (1 if include_nba else 0) + (CBS_PAGES if include_cbs else 0) + 1

        if include_nba:
            if progress_cb:
                progress_cb(step, steps, "Loading NBA Fantasy News…")
            print(f"Loading {FANTASY_NEWS_URL}…", flush=True)
            nba_articles = scrape_fantasy_news_page(driver)
            print(f"Parsed {len(nba_articles)} NBA fantasy news items", flush=True)
            nba_result = _persist_articles(
                conn,
                nba_articles,
                source=SOURCE_NBA,
                scraped_at=scraped_at,
                progress_cb=progress_cb,
                progress_offset=step,
                progress_total=steps,
            )
            by_source["nba"] = nba_result
            sources_run.append(SOURCE_NBA)
            for k in totals:
                totals[k] += int(nba_result.get(k) or 0)
            unmatched_sample.extend(nba_result.get("unmatched_sample") or [])
            step += 1
            conn.commit()

        if include_cbs:
            cbs_articles = scrape_cbs_fantasy_news_pages(
                driver,
                pages=CBS_PAGES,
                progress_cb=lambda done, total, msg: progress_cb(
                    step + done, steps, msg
                )
                if progress_cb
                else None,
            )
            print(f"Parsed {len(cbs_articles)} CBS fantasy news items", flush=True)
            cbs_result = _persist_articles(
                conn,
                cbs_articles,
                source=SOURCE_CBS,
                scraped_at=scraped_at,
                progress_cb=progress_cb,
                progress_offset=step,
                progress_total=steps,
            )
            by_source["cbs"] = cbs_result
            sources_run.append(SOURCE_CBS)
            for k in totals:
                totals[k] += int(cbs_result.get(k) or 0)
            for name in cbs_result.get("unmatched_sample") or []:
                if name not in unmatched_sample:
                    unmatched_sample.append(name)
            step += CBS_PAGES
            conn.commit()

        if progress_cb:
            progress_cb(steps - 1, steps, "Linking news to players…")
        relink = relink_fantasy_news(conn, scraped_at=scraped_at)
        totals["player_news_inserted"] += int(relink.get("player_news_synced") or 0)
        totals["matched"] += int(relink.get("relinked") or 0)
        conn.commit()
        if progress_cb:
            progress_cb(
                steps,
                steps,
                f"Done — {totals['inserted']} new, {totals['skipped']} known",
            )
    finally:
        conn.close()

    return {
        "ok": True,
        "scraped_at": scraped_at,
        "sources": sources_run,
        "by_source": by_source,
        "unmatched_sample": unmatched_sample[:20],
        "source_urls": {
            "nba": FANTASY_NEWS_URL,
            "cbs": f"{CBS_NEWS_BASE}/",
        },
        **totals,
    }
