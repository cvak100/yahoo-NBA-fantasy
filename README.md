# Yahoo NBA Fantasy dashboard

Local Flask app that attaches to a debuggable Chrome session, scrapes your Yahoo Fantasy NBA league, and shows roster / matchup / players / news / injuries tools in a browser UI.

**Privacy:** do not commit `config_local.py` or anything under `data/`. Those hold your league id, team names, and scrape cache. The repo ships anonymized `sample_data/` only.

## Requirements

- Windows (batch helpers included; Linux/macOS work if you start Chrome debug yourself)
- Python 3.11+ recommended
- Google Chrome
- A Yahoo Fantasy NBA league you can open while signed in

## 1. Clone and install

```bash
git clone https://github.com/cvak100/yahoo-NBA-fantasy.git
cd yahoo-NBA-fantasy

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On first import/run, `data/` is created and seeded from `sample_data/` (pre-filled `players.db` + empty league JSON stubs) **only when those files are missing**.

## 2. Set your league id

Copy the example local config and put your Yahoo league number in it:

```bash
copy config_local.example.py config_local.py
```

Edit `config_local.py`:

```python
LEAGUE_ID = "12345"  # from your Yahoo URL: /nba/12345/...
```

Alternatively set env var `YAHOO_LEAGUE_ID` (overrides the local file).

Find the id in the browser address bar while viewing your league, e.g.  
`https://basketball.fantasysports.yahoo.com/nba/12345`.

## 3. Start Chrome in remote-debugging mode

The scraper does **not** log in by itself. It attaches to Chrome on port `9222`.

1. Close **all** Chrome windows.
2. Run:

```bat
start_chrome_debug.bat
```

This starts Chrome with:

- `--remote-debugging-port=9222`
- a project-local profile folder `.chrome_profile/` (Yahoo login persists)

3. In that Chrome window, sign in to Yahoo (if needed) and open your fantasy league.

You can also start Chrome manually:

```bat
"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%CD%\.chrome_profile" ^
  --no-first-run --no-default-browser-check ^
  "https://basketball.fantasysports.yahoo.com/"
```

## 4. Run the app

```bat
start_dashboard.bat
```

or:

```bash
.venv\Scripts\python main.py
```

Open **http://127.0.0.1:5050**

## 5. First refresh

On the Dashboard, click **Refresh** (with Chrome debug still open and your league loaded / accessible). That pulls:

- league settings / standings / my team / matchups
- optional news, injuries, trends, free agents (see Settings)

Players page can refresh ownership + Yahoo **Current** ranks from the league players list.

## Project layout

| Path | Purpose |
|------|---------|
| `main.py` / `app.py` | Flask entry + routes |
| `config.py` | Shared settings (no secrets) |
| `config_local.py` | Your league id (**gitignored**) |
| `sample_data/` | Public starter DB + empty JSON |
| `data/` | Live scrape cache (**gitignored**) |
| `start_chrome_debug.bat` | Chrome with debugger port 9222 |
| `start_dashboard.bat` | Run the app on port 5050 |

## Security notes

- App binds to `127.0.0.1` only.
- `.chrome_profile/` stores your Yahoo session cookies — never share or commit it.
- Live `data/*.json` and `data/players.db` contain your league/team info — gitignored.
- Sample DB owners are anonymized to `FA` / waiver / `Taken` (no real fantasy team names, notes, or watchlist).

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Scraper cannot attach | Chrome must be started with debug port; close other Chrome instances first |
| Empty dashboard | Set `LEAGUE_ID`, open league in the debug Chrome, click Refresh |
| `Request denied` from Yahoo | Slow down / open league home in Chrome, retry |
| Port 5050 busy | Stop the other process or change `APP_PORT` in `config.py` |
