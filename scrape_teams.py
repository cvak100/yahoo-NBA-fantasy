"""Scrape team list from Yahoo Fantasy league page."""

from __future__ import annotations

import re
from dataclasses import dataclass

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config


@dataclass(frozen=True)
class Team:
    team_id: str
    name: str
    manager: str | None = None
    url: str | None = None


_TEAM_HREF_RE = re.compile(
    rf"/nba/{re.escape(config.LEAGUE_ID)}/(\d+)(?:[/?#]|$)"
)


def scrape_teams(driver: WebDriver, *, timeout: float = 20) -> list[Team]:
    """Prebere ekipe iz standings / team linkov na strani lige."""
    wait = WebDriverWait(driver, timeout)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Počakaj na team linke (standings se naloži asinhrono)
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]')
            )
        )
    except Exception:
        pass

    teams_by_id: dict[str, Team] = {}

    # 1) Poskusi standings tabelo (najbolj zanesljivo)
    for row in driver.find_elements(By.CSS_SELECTOR, "#standingstable tbody tr, table.Table tbody tr"):
        links = row.find_elements(
            By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]'
        )
        if not links:
            continue
        link = links[0]
        href = link.get_attribute("href") or ""
        m = _TEAM_HREF_RE.search(href)
        if not m:
            continue
        team_id = m.group(1)
        name = (link.text or "").strip()
        if not name:
            continue

        manager = None
        # Manager je pogosto v sosednji celici / spanu
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        texts = [c.text.strip() for c in cells if c.text.strip()]
        for t in texts:
            if t != name and not t.replace(".", "").isdigit() and "-" not in t[:3]:
                # heuristika: prvo "drugo" besedilo, ki ni W-L
                if re.fullmatch(r"\d+-\d+(-\d+)?", t):
                    continue
                if re.fullmatch(r"[\d.]+", t):
                    continue
                manager = t
                break

        teams_by_id[team_id] = Team(
            team_id=team_id,
            name=name,
            manager=manager,
            url=href.split("?")[0],
        )

    # 2) Fallback: vsi unikatni team linki na strani
    if not teams_by_id:
        for link in driver.find_elements(
            By.CSS_SELECTOR, f'a[href*="/nba/{config.LEAGUE_ID}/"]'
        ):
            href = link.get_attribute("href") or ""
            m = _TEAM_HREF_RE.search(href)
            if not m:
                continue
            team_id = m.group(1)
            # Skip subpages (/nba/{league}/{team}/team, trades, … — team_id is numeric)
            name = (link.text or "").strip()
            if not name or team_id in teams_by_id:
                continue
            # Filtriraj navigacijske linke tipa "Team 1" prazne / ikone
            if len(name) < 2:
                continue
            teams_by_id[team_id] = Team(
                team_id=team_id,
                name=name,
                url=href.split("?")[0],
            )

    teams = sorted(teams_by_id.values(), key=lambda t: int(t.team_id))
    return teams


def format_teams(teams: list[Team]) -> str:
    if not teams:
        return "No teams found."
    lines = [f"League teams ({len(teams)}):", ""]
    for i, team in enumerate(teams, start=1):
        mgr = f"  ({team.manager})" if team.manager else ""
        lines.append(f"  {i:2}. [{team.team_id}] {team.name}{mgr}")
    return "\n".join(lines)
