"""Scrape Yahoo Fantasy player search and available players."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote_plus

from selenium.common.exceptions import StaleElementReferenceException
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


def _guess_stat_slice(texts: list[str], *, status_hint: str | None = None) -> dict[str, str]:
    """
    Find FA/W owner status, then GP / ranks / owned / stats.
    Works across slightly different Yahoo table layouts.
    """
    status_idx = None
    for i, t in enumerate(texts):
        if t in {"FA", "W", "WA"} or t.startswith("W ("):
            status_idx = i
            break

    # Locate %Owned, then walk back: status sits just before GP / ranks.
    owned_idx = None
    for i, t in enumerate(texts):
        if (t or "").endswith("%") and re.fullmatch(r"\d+(\.\d+)?%", t or ""):
            owned_idx = i
            break

    if status_idx is None and owned_idx is not None:
        # Search: GP, Current, %Ros  OR  Players: GP, Pre-Season, Current, %Ros
        if (
            owned_idx >= 2
            and re.fullmatch(r"\d+", texts[owned_idx - 1] or "")
            and re.fullmatch(r"\d+", texts[owned_idx - 2] or "")
        ):
            if (
                owned_idx >= 3
                and re.fullmatch(r"\d+", texts[owned_idx - 3] or "")
            ):
                # GP, Pre, Current, % — status is cell before GP (may be empty/logo)
                status_idx = owned_idx - 4 if owned_idx >= 4 else None
                gp_idx = owned_idx - 3
            else:
                status_idx = owned_idx - 3 if owned_idx >= 3 else None
                gp_idx = owned_idx - 2
            # Never treat GP itself as status (common when Status is logo-only).
            if status_idx is not None:
                cand = texts[status_idx] if 0 <= status_idx < len(texts) else ""
                if re.fullmatch(r"\d+", cand or "") and status_idx == gp_idx:
                    status_idx = None

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

    hint = (status_hint or "").strip()
    if hint:
        out["status"] = hint
    elif status_idx is not None and 0 <= status_idx < len(texts):
        cand = (texts[status_idx] or "").strip()
        # Pure digits here are almost always GP mis-aligned, not a team name.
        if cand and not re.fullmatch(r"\d+", cand):
            out["status"] = cand
        elif not cand:
            # Empty status cell (owned, logo-only) — leave blank for caller to resolve.
            out["status"] = ""
        else:
            out["status"] = ""

    if status_idx is not None and 0 <= status_idx < len(texts):
        rest = texts[status_idx + 1 :]
    elif owned_idx is not None:
        # No status cell text — stats start at GP before %Owned
        if (
            owned_idx >= 3
            and re.fullmatch(r"\d+", texts[owned_idx - 3] or "")
            and re.fullmatch(r"\d+", texts[owned_idx - 2] or "")
            and re.fullmatch(r"\d+", texts[owned_idx - 1] or "")
        ):
            rest = texts[owned_idx - 3 :]
        elif owned_idx >= 2:
            rest = texts[owned_idx - 2 :]
        else:
            rest = texts[owned_idx:]
    else:
        return out

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
    """Parse #players-table via one JS snapshot (avoids StaleElementReference)."""
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#players-table")))

    # Grab all row text/hrefs in one DOM pass — Yahoo often re-renders mid-loop.
    # Roster Status is a team name link (/nba/{league}/{teamId}), NOT a stat cell.
    # (Flat thead th list spans two header rows — never use it for column indexes.)
    league_id = str(getattr(config, "LEAGUE_ID", "") or "")
    raw_rows = driver.execute_script(
        """
        const leagueId = arguments[0] || '';
        const table = document.querySelector('#players-table');
        if (!table) return [];
        const headerRows = [...table.querySelectorAll('thead tr')];
        const lastHeader = headerRows.length
          ? headerRows[headerRows.length - 1]
          : null;
        // Yahoo injects PUA sort glyphs (e.g. Current\\ue002) — strip non-text.
        const colHeaders = lastHeader
          ? [...lastHeader.querySelectorAll('th, td')].map((h) =>
              (h.innerText || h.textContent || '')
                .replace(/[\\uE000-\\uF8FF]/g, '')
                .trim()
                .toLowerCase()
            )
          : [];
        let statusCol = colHeaders.findIndex(
          (h) =>
            h === 'roster status' ||
            h === 'status' ||
            h === 'owner' ||
            h === 'fantasy team'
        );
        // Rankings → Current (sort=AR). Pre-Season is sort=OR — never use as yahoo_rank.
        let currentRankCol = colHeaders.findIndex(
          (h) => h === 'current' || h === 'curr' || h.startsWith('current')
        );
        let preRankCol = colHeaders.findIndex(
          (h) => h === 'pre-season' || h === 'preseason' || h.startsWith('pre-')
        );
        const teamHomeRe = leagueId
          ? new RegExp('^/nba/' + leagueId + '/(\\\\d+)/?$', 'i')
          : /^\\/nba\\/\\d+\\/(\\d+)\\/?$/i;
        const out = [];
        for (const tr of table.querySelectorAll('tbody tr')) {
          const link = tr.querySelector("a[href*='/nba/players/'], a[href*='sports.yahoo.com/nba/players/']");
          if (!link) continue;
          const name = (link.textContent || '').trim();
          if (!name) continue;
          const href = link.getAttribute('href') || '';
          const nameTd = link.closest('td');
          const nameCell = nameTd ? (nameTd.innerText || nameTd.textContent || '') : '';
          const cells = [...tr.querySelectorAll('td')];
          const tds = cells.map((td) => (td.innerText || td.textContent || '').trim());
          let nextMeta = '';
          if (nameTd) {
            const idx = cells.indexOf(nameTd);
            if (idx >= 0 && idx + 1 < cells.length) {
              nextMeta = (cells[idx + 1].innerText || cells[idx + 1].textContent || '').trim();
            }
          }
          let status = '';
          let statusTeamId = '';
          const readTeamLink = (td) => {
            if (!td) return { status: '', teamId: '' };
            const anchors = [...td.querySelectorAll('a[href]')];
            for (const a of anchors) {
              const ah = (a.getAttribute('href') || '').split('?')[0];
              const m = ah.match(teamHomeRe);
              if (!m) continue;
              const label = (a.textContent || a.getAttribute('title') || '').trim();
              return { status: label, teamId: m[1] };
            }
            const t = (td.innerText || td.textContent || '').trim();
            if (t === 'FA' || t === 'W' || t === 'WA' || t.startsWith('W (')) {
              return { status: t, teamId: '' };
            }
            return { status: '', teamId: '' };
          };
          if (statusCol >= 0 && statusCol < cells.length) {
            ({ status, teamId: statusTeamId } = readTeamLink(cells[statusCol]));
          }
          if (!status && !statusTeamId) {
            for (const td of cells) {
              const hit = readTeamLink(td);
              if (hit.status || hit.teamId) {
                status = hit.status;
                statusTeamId = hit.teamId;
                break;
              }
            }
          }
          const cellText = (i) =>
            i >= 0 && i < cells.length
              ? (cells[i].innerText || cells[i].textContent || '').trim()
              : '';
          const currentRank = cellText(currentRankCol);
          const rankPreseason = cellText(preRankCol);
          out.push({
            name,
            href,
            nameCell,
            nextMeta,
            texts: tds,
            status,
            statusTeamId,
            currentRank,
            rankPreseason,
          });
        }
        return out;
        """,
        league_id,
    ) or []

    # team_id → standings name (for logo-only Status cells)
    _resolve = None
    _teams: list[dict[str, Any]] = []
    _team_by_id: dict[str, str] = {}
    try:
        from fantasy_ownership import _resolve_owner_label, _standings_teams

        _resolve = _resolve_owner_label
        _teams = _standings_teams()
        _team_by_id = {
            str(t.get("team_id") or "").strip(): str(t.get("name") or "").strip()
            for t in _teams
            if t.get("team_id") and t.get("name")
        }
    except Exception:
        pass

    players: list[dict[str, Any]] = []
    for row in raw_rows:
        name_raw = str(row.get("name") or "").strip()
        if not name_raw:
            continue
        name, injury = _strip_injury(name_raw)
        profile = str(row.get("href") or "") or None
        m = _PLAYER_ID_RE.search(profile or "")
        player_id = m.group(1) if m else None

        nba_team, positions = "", ""
        name_cell = str(row.get("nameCell") or "")
        lines = [ln.strip() for ln in name_cell.splitlines() if ln.strip()]
        if len(lines) >= 2:
            nba_team, positions = _parse_meta(lines[1])
        else:
            nxt = str(row.get("nextMeta") or "").strip()
            if " - " in nxt or re.fullmatch(r"[A-Z]{2,3}", nxt):
                nba_team, positions = _parse_meta(nxt)

        texts = [str(t or "").strip() for t in (row.get("texts") or [])]
        status_hint = str(row.get("status") or "").strip() or None
        status_team_id = str(row.get("statusTeamId") or "").strip() or None
        # Prefer standings team name over crumb / id.
        if status_team_id and status_team_id in _team_by_id:
            status_hint = _team_by_id[status_team_id]
        elif status_hint and _resolve:
            resolved = _resolve(status_hint, _teams)
            status_hint = resolved
        elif status_hint and re.fullmatch(r"\d+(\.\d+)?%?", status_hint):
            status_hint = None
        stats = _guess_stat_slice(texts, status_hint=status_hint)
        status = (stats["status"] or status_hint or "").strip()
        if not status and status_team_id:
            status = _team_by_id.get(status_team_id) or ""
        if not status:
            status = "FA"
        available = status in {"FA", "W", "WA"} or status.startswith("W")

        # Rankings → Current only. Never keep Pre-Season / heuristic as yahoo_rank.
        current_rank = str(row.get("currentRank") or "").strip()
        pre_rank = str(row.get("rankPreseason") or "").strip()
        if current_rank and re.fullmatch(r"\d+", current_rank):
            stats["rank"] = current_rank
        elif pre_rank and re.fullmatch(r"\d+", pre_rank):
            # Two-column layout (Pre + Current): blank Current → no rank.
            stats["rank"] = ""
        if pre_rank and re.fullmatch(r"\d+", pre_rank):
            stats["rank_preseason"] = pre_rank

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

    def _denied() -> bool:
        try:
            txt = (driver.execute_script(
                "return (document.body && document.body.innerText || '').slice(0, 80);"
            ) or "").strip()
            return "request denied" in txt.lower()
        except Exception:
            return False

    def _warmup() -> None:
        try:
            driver.get(config.LEAGUE_URL)
            time.sleep(max(2.0, float(config.PAGE_LOAD_WAIT_SEC)))
        except Exception:
            pass

    # Yahoo pages are 25 players; ~800–900 NBA names → keep scanning past 500.
    max_offset = max(2000, int(limit) + 100)
    warmed = False
    while len(players) < limit and offset < max_offset:
        url = (
            f"{config.LEAGUE_URL}/players?status={quote_plus(status)}"
            f"&sort={quote_plus(sort)}&count={offset}&jsenabled=1"
        )
        if stat1:
            url += f"&stat1={quote_plus(stat1)}"
        driver.get(url)
        time.sleep(config.PAGE_LOAD_WAIT_SEC)
        if _denied():
            if not warmed:
                print("  Yahoo Request denied — warming via league home…", flush=True)
                _warmup()
                warmed = True
                continue
            print("  Yahoo still denying requests — stopping ranked scrape.", flush=True)
            break
        page: list[dict[str, Any]] = []
        for attempt in range(3):
            try:
                page = _parse_players_table(driver, limit=page_size)
                break
            except StaleElementReferenceException:
                time.sleep(0.6)
                if attempt == 2:
                    page = []
            except Exception:
                if _denied() and not warmed:
                    print("  Yahoo Request denied — warming via league home…", flush=True)
                    _warmup()
                    warmed = True
                    page = []
                    break
                time.sleep(0.6)
                if attempt == 2:
                    page = []
        if not page:
            empty_pages += 1
            if empty_pages >= 2:
                break
            offset += page_size
            continue
        empty_pages = 0
        warmed = False  # ok again; allow future warmups if deny returns
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
