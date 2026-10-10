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
    table = driver.find_elements(By.CSS_SELECTOR, "#standingstable")
    if not table:
        return None

    rows_out: list[dict[str, Any]] = []
    body_rows = table[0].find_elements(By.CSS_SELECTOR, "tbody tr")
    if not body_rows:
        return None

    for i, row in enumerate(body_rows, start=1):
        links = row.find_elements(
            By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]'
        )
        if not links:
            continue
        href = links[0].get_attribute("href") or ""
        m = _TEAM_HREF_RE.search(href)
        if not m:
            continue
        cells = [c.text.strip() for c in row.find_elements(By.CSS_SELECTOR, "td")]
        name = (links[0].text or "").strip()
        rows_out.append(
            {
                "rank": i,
                "team_id": m.group(1),
                "name": name,
                "cells": cells,
                "url": href.split("?")[0],
            }
        )
    return rows_out or None


def scrape_league(driver: WebDriver) -> dict[str, Any]:
    driver.get(config.LEAGUE_URL)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)
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

    if standings_rows:
        board = []
        for row in standings_rows:
            match = next((t for t in teams if t["team_id"] == row["team_id"]), None)
            board.append(
                {
                    "rank": row["rank"],
                    "team_id": row["team_id"],
                    "name": row["name"],
                    "manager": (match or {}).get("manager") or "",
                    "logo": (match or {}).get("logo") or "",
                    "record": "",
                    "url": row.get("url"),
                    "is_mine": row["team_id"] == my_team_id,
                }
            )
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
