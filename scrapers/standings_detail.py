"""Detailed Yahoo standings: WLT/PCT/Waiver/Moves + category totals & averages."""

from __future__ import annotations

import time
from typing import Any, Callable

import config
from jobs import bind_job_driver, raise_if_cancelled, restore_page_load_timeout
from scrapers.league import _scrape_standings_table
from store import load_json, save_json, utc_now_iso

ProgressCb = Callable[..., None]

LEAGUE_STANDINGS_URL = f"{config.LEAGUE_URL}?lhst=stand"
STATS_STANDINGS_URL = f"{config.LEAGUE_URL}/standings?sort=AR"

STAT_KEYS = (
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

_HEADER_MAP = {
    "rank": "rank",
    "#": "rank",
    "team": "name",
    "team name": "name",
    "w-l-t": "wlt",
    "wlt": "wlt",
    "w-l": "wlt",
    "record": "wlt",
    "pct": "pct",
    "win%": "pct",
    "gb": "gb",
    "last week": "last_week",
    "waiver": "waiver",
    "moves": "moves",
    "gp": "gp",
    "fgm/a": "fgm_a",
    "fgm/a*": "fgm_a",
    "fga": "fga",
    "fgm": "fgm",
    "fg%": "fg_pct",
    "ftm/a": "ftm_a",
    "ftm/a*": "ftm_a",
    "fta": "fta",
    "ftm": "ftm",
    "ft%": "ft_pct",
    "3ptm": "threes",
    "3pt": "threes",
    "pts": "pts",
    "reb": "reb",
    "ast": "ast",
    "st": "stl",
    "stl": "stl",
    "blk": "blk",
    "to": "tov",
    "tov": "tov",
    "total": "cat_total",
}


def _team_id_from_href(href: str) -> str:
    marker = f"/nba/{config.LEAGUE_ID}/"
    s = str(href or "")
    i = s.find(marker)
    if i < 0:
        return ""
    j = i + len(marker)
    out = []
    while j < len(s) and s[j].isdigit():
        out.append(s[j])
        j += 1
    return "".join(out)


def _parse_stats_tables(driver) -> dict[str, list[dict[str, Any]]]:
    """Parse /standings?sort=AR tables into totals / averages row lists."""
    league_id = config.LEAGUE_ID
    payload = driver.execute_script(
        """
        const leagueId = arguments[0];
        const headerMap = arguments[1];
        const marker = '/nba/' + leagueId + '/';

        function teamIdFromHref(href) {
          const s = String(href || '');
          const i = s.indexOf(marker);
          if (i < 0) return '';
          let id = '';
          for (let k = i + marker.length; k < s.length; k++) {
            const ch = s[k];
            if (ch < '0' || ch > '9') break;
            id += ch;
          }
          return id;
        }

        function normHeader(h) {
          let s = String(h || '').trim().toLowerCase();
          s = s.replace(/[*∗]/g, '*');
          s = s.replace(/\\s+/g, ' ');
          return headerMap[s] || s;
        }

        // Build column map from the last header row that contains Rank/Team/FG%.
        function columnKeys(table) {
          const rows = [...table.querySelectorAll('thead tr, tr')].filter((tr) =>
            tr.querySelectorAll('th').length
          );
          // Prefer the header row that includes concrete stat labels.
          let best = [];
          for (const tr of rows) {
            const keys = [...tr.querySelectorAll('th')].map((th) =>
              normHeader(th.textContent || '')
            );
            const score = keys.filter((k) =>
              ['rank','name','fg_pct','ft_pct','pts','reb','ast','stl','blk','tov','threes','gp','fgm_a','ftm_a'].includes(k)
            ).length;
            if (score > best.filter((k) =>
              ['rank','name','fg_pct','ft_pct','pts','reb','ast','stl','blk','tov','threes','gp','fgm_a','ftm_a'].includes(k)
            ).length) {
              best = keys;
            }
          }
          return best;
        }

        const tables = [...document.querySelectorAll('table')];
        const parsed = [];
        for (const table of tables) {
          const keys = columnKeys(table);
          if (!keys.includes('name') && !keys.includes('rank')) continue;
          if (!keys.some((k) => ['fg_pct','pts','reb','ast','threes'].includes(k))) continue;
          const rows = [];
          for (const tr of table.querySelectorAll('tbody tr')) {
            const tds = [...tr.querySelectorAll('td')];
            if (!tds.length) continue;
            const links = [...tr.querySelectorAll('a[href]')].filter((a) =>
              teamIdFromHref(a.getAttribute('href') || a.href || '')
            );
            if (!links.length) continue;
            const nameLink = links.find((a) => (a.textContent || '').trim()) || links[0];
            const href = (nameLink.getAttribute('href') || '').split('?')[0];
            const teamId = teamIdFromHref(href);
            const row = {
              team_id: teamId,
              name: (nameLink.textContent || '').replace(/\\s+/g, ' ').trim(),
              url: href.startsWith('http')
                ? href
                : ('https://basketball.fantasysports.yahoo.com' + href),
            };
            // Align cells to header keys from the right if counts differ
            // (Yahoo often inserts empty leading TDs for logos / rank icons).
            const offset = Math.max(0, tds.length - keys.length);
            keys.forEach((key, i) => {
              if (!key || key === 'name') return;
              const td = tds[i + offset] || tds[i];
              if (!td) return;
              const val = (td.textContent || '').replace(/\\s+/g, ' ').trim();
              if (key === 'rank') {
                const n = parseInt(val, 10);
                row.rank = Number.isFinite(n) ? n : val;
              } else {
                row[key] = val;
              }
            });
            if (!row.name && !row.team_id) continue;
            rows.push(row);
          }
          if (rows.length) {
            parsed.push({
              keys,
              has_gp: keys.includes('gp'),
              has_total: keys.includes('cat_total'),
              rows,
            });
          }
        }
        return parsed;
        """,
        league_id,
        _HEADER_MAP,
    )

    totals: list[dict[str, Any]] = []
    averages: list[dict[str, Any]] = []
    for block in payload or []:
        rows = list(block.get("rows") or [])
        # Table with GP is usually season totals; without GP + with Total is points board.
        # For avg mode Yahoo may use the same columns with different values after toggle.
        if block.get("has_gp") or not block.get("has_total"):
            # Prefer GP table as season TOTALS.
            if block.get("has_gp") or not totals:
                tagged = []
                for row in rows:
                    item = {
                        "team_id": row.get("team_id"),
                        "name": row.get("name"),
                        "url": row.get("url"),
                        "rank": row.get("rank"),
                        "gp": row.get("gp"),
                    }
                    for key in STAT_KEYS:
                        if row.get(key) not in (None, ""):
                            item[key] = row.get(key)
                            item[f"total_{key}"] = row.get(key)
                    # Compose FGM/A / FTM/A if Yahoo splits made/attempted.
                    if not item.get("fgm_a") and row.get("fgm") and row.get("fga"):
                        item["fgm_a"] = f"{row.get('fgm')}/{row.get('fga')}"
                        item["total_fgm_a"] = item["fgm_a"]
                    if not item.get("ftm_a") and row.get("ftm") and row.get("fta"):
                        item["ftm_a"] = f"{row.get('ftm')}/{row.get('fta')}"
                        item["total_ftm_a"] = item["ftm_a"]
                    tagged.append(item)
                if block.get("has_gp"):
                    totals = tagged
                elif not totals:
                    totals = tagged
        # Keep a secondary copy as averages if a later toggle fills them.
        if not averages and rows:
            tagged_avg = []
            for row in rows:
                item = {
                    "team_id": row.get("team_id"),
                    "name": row.get("name"),
                    "url": row.get("url"),
                }
                for key in STAT_KEYS:
                    if row.get(key) not in (None, ""):
                        item[f"avg_{key}"] = row.get(key)
                if item.get("avg_fg_pct") or item.get("avg_pts"):
                    tagged_avg.append(item)
            if tagged_avg and block.get("has_gp"):
                # Same values initially; real avg filled after toggle if available.
                pass

    return {"totals": totals, "averages": averages}


def _select_stat_mode(driver, *, want_avg: bool) -> bool:
    return bool(
        driver.execute_script(
            """
            const wantAvg = !!arguments[0];
            const needles = wantAvg
              ? [/average/i, /\\bavg\\b/i]
              : [/\\btotal/i, /totals/i];
            for (const sel of document.querySelectorAll('select')) {
              const opts = [...sel.options];
              const hit = opts.find((o) => needles.some((re) => re.test(o.textContent || '')));
              if (hit && String(sel.value) !== String(hit.value)) {
                sel.value = hit.value;
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
            }
            const clickables = [...document.querySelectorAll('a, button, label, span, li')];
            for (const el of clickables) {
              const t = (el.textContent || '').trim();
              if (!t || t.length > 24) continue;
              if (needles.some((re) => re.test(t))) {
                try { el.click(); return true; } catch (e) {}
              }
            }
            return false;
            """,
            want_avg,
        )
    )


def _merge_by_team(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group or []:
            tid = str(row.get("team_id") or "").strip()
            if not tid:
                name = str(row.get("name") or "").strip().lower()
                if not name:
                    continue
                # Find existing by name
                match = next(
                    (
                        v
                        for v in by_id.values()
                        if str(v.get("name") or "").strip().lower() == name
                    ),
                    None,
                )
                if match is None:
                    # provisional key
                    tid = f"name:{name}"
                    by_id[tid] = dict(row)
                else:
                    for k, v in row.items():
                        if v not in (None, "") or match.get(k) in (None, ""):
                            if v not in (None, ""):
                                match[k] = v
                continue
            prev = by_id.get(tid)
            if prev is None:
                by_id[tid] = dict(row)
            else:
                for k, v in row.items():
                    if v not in (None, ""):
                        prev[k] = v
                    elif k not in prev:
                        prev[k] = v

    rows = [r for k, r in by_id.items() if not str(k).startswith("name:")]
    # orphan name-only rows
    for k, r in by_id.items():
        if str(k).startswith("name:"):
            rows.append(r)

    def sort_key(r: dict[str, Any]) -> tuple:
        try:
            rank = int(r.get("rank") or 999)
        except (TypeError, ValueError):
            rank = 999
        try:
            tid = int(r.get("team_id") or 999)
        except (TypeError, ValueError):
            tid = 999
        return (rank, tid)

    rows.sort(key=sort_key)
    for i, row in enumerate(rows, start=1):
        if row.get("rank") in (None, ""):
            row["rank"] = i
    return rows


def scrape_standings_detail(driver) -> dict[str, Any]:
    """Scrape WLT board + season category stats into one merged table."""
    raise_if_cancelled()

    # 1) Classic standings: W-L-T / Pct / Waiver / Moves
    driver.get(LEAGUE_STANDINGS_URL)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    try:
        cur = driver.current_url or ""
        if f"/nba/{config.LEAGUE_ID}/" in cur and "lhst=stand" not in cur:
            # landed on a team page — force standings module
            driver.get(LEAGUE_STANDINGS_URL)
            time.sleep(config.PAGE_LOAD_WAIT_SEC)
    except Exception:
        pass

    board_rows = _scrape_standings_table(driver) or []
    ranking_rows: list[dict[str, Any]] = []
    for row in board_rows:
        ranking_rows.append(
            {
                "rank": row.get("rank"),
                "team_id": row.get("team_id"),
                "name": row.get("name"),
                "logo": row.get("logo"),
                "url": row.get("url"),
                "wlt": row.get("record") or row.get("wlt") or "",
                "pct": row.get("pct") or "",
                "waiver": row.get("waiver") or "",
                "moves": row.get("moves") or "",
                "gb": (row.get("cells") or [None] * 5)[4]
                if isinstance(row.get("cells"), list) and len(row.get("cells") or []) > 4
                else row.get("gb"),
                "last_week": (row.get("cells") or [None] * 6)[5]
                if isinstance(row.get("cells"), list) and len(row.get("cells") or []) > 5
                else row.get("last_week"),
            }
        )

    raise_if_cancelled()

    # 2) Category season stats (totals)
    driver.get(STATS_STANDINGS_URL)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    _select_stat_mode(driver, want_avg=False)
    time.sleep(0.8)
    parsed = _parse_stats_tables(driver)
    total_rows = parsed.get("totals") or []

    # 3) Category season averages (if Yahoo exposes a toggle)
    avg_rows: list[dict[str, Any]] = []
    if _select_stat_mode(driver, want_avg=True):
        time.sleep(max(1.2, float(config.PAGE_LOAD_WAIT_SEC) * 0.45))
        avg_parsed = _parse_stats_tables(driver)
        for row in avg_parsed.get("totals") or []:
            item = {
                "team_id": row.get("team_id"),
                "name": row.get("name"),
                "url": row.get("url"),
            }
            for key in STAT_KEYS:
                val = row.get(f"total_{key}") or row.get(key)
                if val not in (None, ""):
                    item[f"avg_{key}"] = val
            avg_rows.append(item)

    merged = _merge_by_team(ranking_rows, total_rows, avg_rows)

    # Enrich is_mine from simple standings JSON
    simple = load_json(config.STANDINGS_PATH, {}) or {}
    by_simple = {
        str(r.get("team_id")): r for r in (simple.get("rows") or []) if r.get("team_id")
    }
    for row in merged:
        tid = str(row.get("team_id") or "")
        s = by_simple.get(tid) or {}
        if not row.get("name") and s.get("name"):
            row["name"] = s.get("name")
        if s.get("logo") and not row.get("logo"):
            row["logo"] = s.get("logo")
        if s.get("manager"):
            row["manager"] = s.get("manager")
        row["is_mine"] = bool(s.get("is_mine")) or bool(row.get("is_mine"))
        if not row.get("url") and tid:
            row["url"] = f"{config.LEAGUE_URL}/{tid}"

    return {
        "source_url": LEAGUE_STANDINGS_URL,
        "stats_url": STATS_STANDINGS_URL,
        "rows": merged,
        "has_totals": any(
            (r.get(f"total_{k}") or r.get(k)) not in (None, "", "-")
            for r in merged
            for k in STAT_KEYS
        ),
        "has_averages": any(
            r.get(f"avg_{k}") not in (None, "", "-") for r in merged for k in STAT_KEYS
        ),
        "stat_keys": list(STAT_KEYS),
    }


def sync_standings_detail(*, progress_cb: ProgressCb | None = None) -> dict[str, Any]:
    from chrome_browser import attach_chrome

    if progress_cb:
        progress_cb(0, 3, "Attaching Chrome…")
    driver = attach_chrome()
    restore_page_load_timeout(driver)
    bind_job_driver(driver)
    if progress_cb:
        progress_cb(1, 3, "Loading WLT + category stats…")
    raise_if_cancelled()
    detail = scrape_standings_detail(driver)
    if progress_cb:
        progress_cb(2, 3, "Saving standings…")
    scraped_at = utc_now_iso()
    payload = {
        "ok": True,
        "scraped_at": scraped_at,
        "league_id": config.LEAGUE_ID,
        **detail,
        "count": len(detail.get("rows") or []),
    }
    save_json(config.STANDINGS_DETAIL_PATH, payload)
    if progress_cb:
        progress_cb(3, 3, f"Done — {payload['count']} teams")
    return payload


def load_standings_detail() -> dict[str, Any]:
    data = load_json(config.STANDINGS_DETAIL_PATH, None)
    if not data:
        return {
            "ok": True,
            "scraped_at": None,
            "rows": [],
            "count": 0,
            "has_totals": False,
            "has_averages": False,
            "stat_keys": list(STAT_KEYS),
            "note": "No detailed standings yet. Click Refresh standings.",
        }
    return data
