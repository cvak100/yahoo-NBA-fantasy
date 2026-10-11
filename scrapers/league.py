"""Scrape league home: my team, teams list, standings (or pre-draft board)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

import config

_TEAM_HREF_RE = re.compile(
    rf"/nba/{re.escape(config.LEAGUE_ID)}/(\d+)(?:[/?#]|$)"
)


def _extract_js_string(html: str, key: str) -> str | None:
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', html)
    if not m:
        return None
    return bytes(m.group(1), "utf-8").decode("unicode_escape")


def _parse_yahoo_team_keys(html: str) -> list[dict[str, str]]:
    """Parse varPRLeagueTeams into [{team_id, name}] using /nba/LEAGUE/ID links later."""
    m = re.search(r'"varPRLeagueTeams"\s*:\s*(\{.*?\})', html, re.S)
    if not m:
        return []
    raw = m.group(1).replace("\\'", "'")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    teams: list[dict[str, str]] = []
    for key, name in data.items():
        key_s = str(key)
        # Observed: 9249605 => team 2; 1049605 => team 10 (league 49605)
        if key_s.endswith(config.LEAGUE_ID) and len(key_s) > len(config.LEAGUE_ID):
            prefix = key_s[: -len(config.LEAGUE_ID)]
            if len(prefix) == 2 and prefix.startswith("9"):
                team_id = prefix[1:]
            else:
                team_id = prefix
            teams.append({"team_id": team_id, "name": name})
    teams.sort(key=lambda t: int(t["team_id"]) if t["team_id"].isdigit() else 999)
    return teams


def _teams_from_invite_json(driver: WebDriver) -> list[dict[str, Any]]:
    el = driver.find_elements(By.CSS_SELECTOR, "#teams[data-invite-data]")
    if not el:
        return []
    raw = el[0].get_attribute("data-invite-data") or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    confirmed = data.get("confirmedList") or []
    out = []
    for i, row in enumerate(confirmed, start=1):
        out.append(
            {
                "team_id": str(i),  # provisional; refined by hrefs
                "name": row.get("teamName") or "",
                "manager": row.get("name") or "",
                "joined": row.get("joined") or "",
                "logo": row.get("image") or "",
                "rank": row.get("team_rank") or None,
            }
        )
    return out


def _merge_team_ids_from_links(
    driver: WebDriver, teams: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    name_to_id: dict[str, str] = {}
    for a in driver.find_elements(
        By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]'
    ):
        href = a.get_attribute("href") or ""
        m = _TEAM_HREF_RE.search(href)
        if not m:
            continue
        text = (a.text or "").strip()
        if text:
            name_to_id[text] = m.group(1)

    for t in teams:
        tid = name_to_id.get(t["name"])
        if tid:
            t["team_id"] = tid
            t["url"] = f"{config.LEAGUE_URL}/{tid}"
    return teams


def _scrape_standings_table(driver: WebDriver) -> list[dict[str, Any]] | None:
    """Parse standings; Yahoo uses a logo link + a separate name link in the Team cell."""
    league_id = config.LEAGUE_ID
    rows = driver.execute_script(
        """
        const leagueId = arguments[0];
        const table = document.querySelector('#standingstable');
        if (!table) return null;
        const trs = [...table.querySelectorAll('tbody tr')];
        if (!trs.length) return null;
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
        const out = [];
        for (let idx = 0; idx < trs.length; idx++) {
          const tr = trs[idx];
          const links = [...tr.querySelectorAll('a[href]')].filter((a) =>
            teamIdFromHref(a.getAttribute('href') || a.href || '')
          );
          if (!links.length) continue;
          // Prefer the link that has visible team name text; logo link is often empty.
          const nameLink = links.find((a) => (a.textContent || '').trim()) || links[0];
          const logoLink = links.find((a) => a.querySelector('img')) || links[0];
          const href = (nameLink.getAttribute('href') || logoLink.getAttribute('href') || '').split('?')[0];
          const teamId = teamIdFromHref(href);
          if (!teamId) continue;
          const cells = [...tr.querySelectorAll('td')].map((td) =>
            (td.textContent || '').replace(/\\s+/g, ' ').trim()
          );
          let name = (nameLink.textContent || '').replace(/\\s+/g, ' ').trim();
          if (!name) {
            const teamCell = tr.querySelectorAll('td')[1] || nameLink.closest('td');
            name = ((teamCell && teamCell.textContent) || '').replace(/\\s+/g, ' ').trim();
          }
          const img = logoLink.querySelector('img');
          const logo = img ? (img.getAttribute('src') || '') : '';
          let record = '';
          for (const c of cells) {
            if (/^\\d+-\\d+(?:-\\d+)?$/.test(c)) { record = c; break; }
          }
          // Typical cols: [rankmark, team, W-L-T, Pct, GB, Last Week, Waiver, Moves]
          out.push({
            rank: idx + 1,
            team_id: teamId,
            name,
            record,
            pct: cells[3] || '',
            waiver: cells[6] || '',
            moves: cells[7] || '',
            logo,
            cells,
            url: href.startsWith('http')
              ? href
              : ('https://basketball.fantasysports.yahoo.com' + href),
          });
        }
        return out.length ? out : null;
        """,
        league_id,
    )
    if not rows:
        return None
    return list(rows)


def scrape_league(driver: WebDriver) -> dict[str, Any]:
    # Force league home standings module — bare /nba/{id} sometimes lands on My Team.
    driver.get(f"{config.LEAGUE_URL}?lhst=stand")
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
    # If Yahoo still dropped us on a team page, bounce back to standings.
    try:
        cur = driver.current_url or ""
        if re.search(rf"/nba/{re.escape(config.LEAGUE_ID)}/\d+", cur) and "lhst=stand" not in cur:
            driver.get(f"{config.LEAGUE_URL}?lhst=stand")
            time.sleep(config.PAGE_LOAD_WAIT_SEC)
    except Exception:
        pass
    html = driver.page_source

    my_team_name = _extract_js_string(html, "varPRCurrTeamName") or ""
    parsed_teams = _parse_yahoo_team_keys(html)

    teams = _teams_from_invite_json(driver)
    if not teams and parsed_teams:
        teams = [{**t, "manager": "", "joined": "", "logo": ""} for t in parsed_teams]

    teams = _merge_team_ids_from_links(driver, teams)

    # If invite list team_ids still provisional, prefer parsed map
    if parsed_teams:
        by_name = {t["name"]: t["team_id"] for t in parsed_teams}
        for t in teams:
            if t["name"] in by_name:
                t["team_id"] = by_name[t["name"]]
                t["url"] = f"{config.LEAGUE_URL}/{t['team_id']}"

    my_team_id = None
    my_manager = None
    my_logo = None
    for t in teams:
        if my_team_name and t["name"] == my_team_name:
            my_team_id = t["team_id"]
            my_manager = t.get("manager")
            my_logo = t.get("logo")
            break

    standings_rows = _scrape_standings_table(driver)
    pre_draft = standings_rows is None

    # Index helpers for filling blank standings cells after draft.
    teams_by_id = {str(t.get("team_id")): t for t in teams if t.get("team_id") is not None}
    teams_by_name = {str(t.get("name") or "").strip(): t for t in teams if t.get("name")}

    if standings_rows:
        board = []
        for row in standings_rows:
            tid = str(row.get("team_id") or "")
            match = teams_by_id.get(tid)
            if match is None and row.get("name"):
                match = teams_by_name.get(str(row.get("name") or "").strip())
            name = (row.get("name") or (match or {}).get("name") or "").strip()
            board.append(
                {
                    "rank": row["rank"],
                    "team_id": tid,
                    "name": name,
                    "manager": (match or {}).get("manager") or "",
                    "logo": (row.get("logo") or (match or {}).get("logo") or ""),
                    "record": (row.get("record") or "").strip() or "0-0-0",
                    "pct": (row.get("pct") or "").strip(),
                    "waiver": (row.get("waiver") or "").strip(),
                    "moves": (row.get("moves") or "").strip(),
                    "url": row.get("url") or f"{config.LEAGUE_URL}/{tid}",
                    "is_mine": tid == str(my_team_id) if my_team_id else False,
                }
            )
        # Back-fill teams list from standings when invite JSON is gone post-draft.
        if board and (not teams or all(not (t.get("name") or "").strip() for t in teams)):
            teams = [
                {
                    "team_id": r["team_id"],
                    "name": r["name"],
                    "manager": r.get("manager") or "",
                    "logo": r.get("logo") or "",
                    "url": r.get("url"),
                    "is_mine": r.get("is_mine"),
                }
                for r in board
            ]
        elif board:
            for r in board:
                t = teams_by_id.get(str(r["team_id"]))
                if t is not None and not (t.get("name") or "").strip() and r.get("name"):
                    t["name"] = r["name"]
    else:
        board = []
        for i, t in enumerate(
            sorted(teams, key=lambda x: int(x["team_id"]) if str(x["team_id"]).isdigit() else 999),
            start=1,
        ):
            board.append(
                {
                    "rank": i,
                    "team_id": t["team_id"],
                    "name": t["name"],
                    "manager": t.get("manager") or "",
                    "logo": t.get("logo") or "",
                    "record": "0-0",
                    "url": t.get("url") or f"{config.LEAGUE_URL}/{t['team_id']}",
                    "is_mine": t["team_id"] == my_team_id or t["name"] == my_team_name,
                    "joined": t.get("joined") or "",
                }
            )

    return {
        "league_id": config.LEAGUE_ID,
        "my_team": {
            "team_id": my_team_id,
            "name": my_team_name,
            "manager": my_manager,
            "logo": my_logo,
            "url": f"{config.LEAGUE_URL}/{my_team_id}" if my_team_id else None,
        },
        "teams": teams,
        "standings": {
            "pre_draft": pre_draft,
            "rows": board,
        },
        "source_url": config.LEAGUE_URL,
    }
