"""CLI: sync Yahoo data or run Flask app."""

from __future__ import annotations

import subprocess
import sys
import time

import config


def _kill_port(port: int) -> None:
    """Force-free a TCP listen port on Windows (stale Flask leftovers)."""
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return
    pids: set[int] = set()
    needle = f":{port}"
    for line in out.splitlines():
        if needle not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0:
            pids.add(pid)
    for pid in pids:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
        )
    if pids:
        time.sleep(0.8)


def cmd_sync() -> int:
    from chrome_browser import ChromeNotOpenError
    from scrapers.sync import sync_all

    try:
        result = sync_all()
    except ChromeNotOpenError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    meta = result["meta"]
    my = result["my_team"]
    standings = result["standings"]
    print(f"League:    {meta.get('league_name')} ({meta.get('league_id')})")
    print(f"My team:   {my.get('name')} [#{my.get('team_id')}]")
    print(f"Slots:     {my.get('capacity')}")
    print(
        f"Standings: {len(standings.get('rows') or [])} teams "
        f"(pre_draft={standings.get('pre_draft')})"
    )
    mu = result.get("matchups") or {}
    mine = mu.get("my_matchup") or {}
    print(
        f"Matchup:   Week {mu.get('week')} · "
        f"{(mine.get('home') or {}).get('name')} vs {(mine.get('away') or {}).get('name')} · "
        f"{len(mu.get('matchups') or [])} games"
    )
    print(f"Synced:    {meta.get('synced_at')}")
    print("Saved to data/*.json")
    return 0


def cmd_serve() -> int:
    import os

    from app import app

    host = config.APP_HOST
    port = config.APP_PORT
    # Only clear the port on the outer process — not on Werkzeug reloader child.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        _kill_port(port)
    print(f"Dashboard: http://{host}:{port}")
    print(f"Players:   http://{host}:{port}/players")
    print(f"Watchlist: http://{host}:{port}/watchlist")
    # Templates/static reload without restart; Python files auto-reload via reloader.
    # Note: saving .py mid-scrape will interrupt that job — wait for jobs to finish first.
    app.run(
        host=host,
        port=port,
        debug=True,
        use_reloader=True,
        use_debugger=False,
        threaded=True,
        exclude_patterns=[
            "*/data/*",
            "*/.venv/*",
            "*/__pycache__/*",
            "*/.git/*",
            "*/agent-transcripts/*",
            "*/terminals/*",
        ],
    )
    return 0


def cmd_sync_players() -> int:
    from chrome_browser import ChromeNotOpenError
    from scrapers.player_db import sync_player_database

    limit = 10
    args = sys.argv[2:]
    if args and args[0].isdigit():
        limit = int(args[0])

    try:
        result = sync_player_database(limit=limit)
    except ChromeNotOpenError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Synced top {result['limit']} players into data/players.db")
    for k, v in (result.get("stats") or {}).items():
        print(f"  {k}: {v}")
    return 0


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in {"serve", "app"}:
        raise SystemExit(cmd_serve())
    if args[0] == "sync":
        raise SystemExit(cmd_sync())
    if args[0] in {"sync-players", "players"}:
        raise SystemExit(cmd_sync_players())
    print("Usage: python main.py [serve|sync|sync-players [N]]", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
