from __future__ import annotations

import time
from collections import Counter
from urllib.parse import quote_plus

import config
from chrome_browser import attach_chrome
from scrapers.player_db import scrape_player_overlay

d = attach_chrome()
d.get(f"{config.LEAGUE_URL}/playersearch?search={quote_plus('Nikola Jokic')}")
time.sleep(config.PAGE_LOAD_WAIT_SEC)
detail = scrape_player_overlay(d, "5352")
rows = detail["season_rows"]
print("count", len(rows))
print(Counter(r["record_type"] for r in rows))
for r in sorted(rows, key=lambda x: (x["record_type"], x["season"]), reverse=True):
    print(
        r["record_type"],
        r["season"],
        "GP",
        r.get("games_played"),
        "Rk",
        r.get("season_rank"),
        "PTS",
        r.get("pts"),
        "REB",
        r.get("reb"),
        "AST",
        r.get("ast"),
    )
