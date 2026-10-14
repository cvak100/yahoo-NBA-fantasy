"""Yahoo Fantasy scraper + app configuration.

Set your league id in ``config_local.py`` (copy from ``config_local.example.py``)
or via the ``YAHOO_LEAGUE_ID`` environment variable. Never commit a real league id.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Public default is a placeholder — override locally.
LEAGUE_ID = "YOUR_LEAGUE_ID"
try:
    from config_local import LEAGUE_ID as _LOCAL_LEAGUE_ID  # type: ignore

    if str(_LOCAL_LEAGUE_ID or "").strip():
        LEAGUE_ID = str(_LOCAL_LEAGUE_ID).strip()
except ImportError:
    pass

_env_league = os.environ.get("YAHOO_LEAGUE_ID", "").strip()
if _env_league:
    LEAGUE_ID = _env_league

LEAGUE_URL = f"https://basketball.fantasysports.yahoo.com/nba/{LEAGUE_ID}"
SETTINGS_URL = f"{LEAGUE_URL}/settings"
NBA_FANTASY_NEWS_URL = "https://www.nba.com/stats/fantasynews"

# Single fixed dashboard port — never hop around.
APP_HOST = "127.0.0.1"
APP_PORT = 5050

CHROME_DEBUGGER_ADDRESS = "127.0.0.1:9222"
PAGE_LOAD_WAIT_SEC = 4

DATA_DIR = BASE_DIR / "data"
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
RULES_PATH = DATA_DIR / "rules.json"
STANDINGS_PATH = DATA_DIR / "standings.json"
STANDINGS_DETAIL_PATH = DATA_DIR / "standings_detail.json"
MY_TEAM_PATH = DATA_DIR / "my_team.json"
MATCHUPS_PATH = DATA_DIR / "matchups.json"
META_PATH = DATA_DIR / "meta.json"
APP_SETTINGS_PATH = DATA_DIR / "app_settings.json"
REFRESH_TIMINGS_PATH = DATA_DIR / "refresh_timings.json"
FREE_PLAYERS_PATH = DATA_DIR / "free_players.json"
CAT_HISTORY_PATH = DATA_DIR / "cat_history.json"
DB_PATH = DATA_DIR / "players.db"

# Daily / main refresh defaults
DEFAULT_REFRESH_AVAILABLE_COUNT = 100
MIN_REFRESH_AVAILABLE_COUNT = 1
MAX_REFRESH_AVAILABLE_COUNT = 500

POSITION_LABELS = {
    "PG": "Point Guard",
    "SG": "Shooting Guard",
    "G": "Guard",
    "SF": "Small Forward",
    "PF": "Power Forward",
    "F": "Forward",
    "C": "Center",
    "Util": "Utility",
    "BN": "Bench",
    "IL": "Injured List",
    "IL+": "Injured List+",
    "IR": "Injured Reserve",
}

STARTER_POSITIONS = {"PG", "SG", "G", "SF", "PF", "F", "C", "Util"}


def ensure_runtime_data() -> None:
    """Create ``data/`` and seed from ``sample_data/`` when files are missing."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "team_snapshots").mkdir(parents=True, exist_ok=True)
    if not SAMPLE_DATA_DIR.is_dir():
        return
    # Pre-filled NBA players DB (ownership anonymized).
    sample_db = SAMPLE_DATA_DIR / "players.db"
    if sample_db.is_file() and not DB_PATH.exists():
        shutil.copy2(sample_db, DB_PATH)
    for name in (
        "app_settings.json",
        "meta.json",
        "rules.json",
        "standings.json",
        "my_team.json",
        "matchups.json",
    ):
        src = SAMPLE_DATA_DIR / name
        dest = DATA_DIR / name
        if src.is_file() and not dest.exists():
            shutil.copy2(src, dest)


ensure_runtime_data()
