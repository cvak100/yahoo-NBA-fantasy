"""Scrape league scoring & settings / rules."""

from __future__ import annotations

import re
import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

import config


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"\[\s*Add to My Calendar\s*\].*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*Edit Draft\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s*Convert to cash league\s*$", "", t, flags=re.I).strip()
    return t


def _parse_roster_positions(raw: str) -> list[dict[str, Any]]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    slots: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for pos in parts:
        n = counts.get(pos, 0) + 1
        counts[pos] = n
        slots.append(
            {
                "position": pos,
                "slot_index": n,
                "label": config.POSITION_LABELS.get(pos, pos),
                "group": (
                    "bench"
                    if pos == "BN"
                    else "il"
                    if pos in {"IL", "IL+", "IR"}
                    else "starter"
                ),
            }
        )
    return slots


def scrape_settings(driver: WebDriver) -> dict[str, Any]:
    driver.get(config.SETTINGS_URL)
    time.sleep(config.PAGE_LOAD_WAIT_SEC)

    settings: dict[str, str] = {}
    for row in driver.find_elements(By.CSS_SELECTOR, "#settings-table tr"):
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        if len(cells) < 2:
            continue
        key = _clean(cells[0].text).rstrip(":")
        value = _clean(cells[1].text)
        if key and value:
            settings[key] = value

    roster_raw = settings.get("Roster Positions", "")
    slots = _parse_roster_positions(roster_raw) if roster_raw else []

    categories_raw = settings.get("Players Stat Categories", "")
    categories = [c.strip() for c in categories_raw.split(",") if c.strip()]

    return {
        "league_id": config.LEAGUE_ID,
        "league_name": settings.get("League Name", ""),
        "settings": settings,
        "roster_positions": slots,
        "roster_positions_raw": roster_raw,
        "stat_categories": categories,
        "source_url": config.SETTINGS_URL,
    }
