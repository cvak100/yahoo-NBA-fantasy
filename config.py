"""Yahoo Fantasy scraper + app configuration."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

LEAGUE_ID = "49605"
LEAGUE_URL = f"https://basketball.fantasysports.yahoo.com/nba/{LEAGUE_ID}"
SETTINGS_URL = f"{LEAGUE_URL}/settings"

# Single fixed dashboard port — never hop around.
APP_HOST = "127.0.0.1"
APP_PORT = 5050

# Dev serving: templates/static always fresh; Python uses Werkzeug reloader in main.py.

CHROME_DEBUGGER_ADDRESS = "127.0.0.1:9222"
PAGE_LOAD_WAIT_SEC = 4

DATA_DIR = BASE_DIR / "data"
RULES_PATH = DATA_DIR / "rules.json"
STANDINGS_PATH = DATA_DIR / "standings.json"
MY_TEAM_PATH = DATA_DIR / "my_team.json"
MATCHUPS_PATH = DATA_DIR / "matchups.json"
META_PATH = DATA_DIR / "meta.json"
DB_PATH = DATA_DIR / "players.db"

# Pozicije: kratice -> prikaz
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
