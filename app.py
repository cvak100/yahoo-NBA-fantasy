"""Flask dashboard for Yahoo Fantasy league."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request

import config
from chrome_browser import ChromeNotOpenError, attach_chrome
from db import (
    add_favourite,
    clear_players_db,
    clear_watchlist,
    delete_player,
    delete_players,
    get_player_by_yahoo_id,
    get_player_detail,
    get_yahoo_ids,
    list_players,
    list_watchlist_by_position,
    remove_favourite,
)
from jobs import active_job, get_job, scrape_busy, start_job
from scrapers.player_db import (
    add_missing_players,
    add_player_to_db,
    refresh_all_players_in_db,
    refresh_player_in_db,
)
from scrapers.players import search_players, top_available
from scrapers.sync import sync_all
from store import load_dashboard

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True


@app.after_request
def _no_cache_dev(resp):
    """Always serve fresh HTML/CSS/JS so UI edits show without restarting."""
    path = request.path or ""
    ctype = (resp.content_type or "").split(";")[0].strip().lower()
    if path.startswith("/static/") or ctype in {"text/html", "text/css", "application/javascript", "text/javascript"}:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.errorhandler(404)
def api_not_found(err):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Not found.", "path": request.path}), 404
    return err.get_response()


@app.errorhandler(500)
def api_server_error(err):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": str(getattr(err, "original_exception", err))}), 500
    return err.get_response()


@app.get("/api/ping")
def api_ping():
    return jsonify(
        {
            "ok": True,
            "app": "yahoonba",
            "bulk_add": True,
            "scrape_busy": scrape_busy(),
            "active_job": active_job(),
        }
    )


@app.get("/api/jobs/active")
def api_jobs_active():
    job = active_job()
    return jsonify({"ok": True, "job": job, "scrape_busy": scrape_busy()})


@app.get("/api/jobs/<job_id>")
def api_jobs_get(job_id: str):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Job not found."}), 404
    return jsonify({"ok": True, "job": job})


@app.get("/")
def index():
    return render_template("index.html", league_id=config.LEAGUE_ID, active="home")


@app.get("/search")
def search_page():
    return render_template("search.html", league_id=config.LEAGUE_ID, active="search")


@app.get("/watchlist")
def watchlist_page():
    return render_template("watchlist.html", league_id=config.LEAGUE_ID, active="watchlist")


@app.get("/settings")
def settings_page():
    return render_template("settings.html", league_id=config.LEAGUE_ID, active="settings")


@app.get("/api/dashboard")
def api_dashboard():
    data = load_dashboard()
    has_data = bool(data.get("meta") or data.get("my_team") or data.get("standings"))
    return jsonify({"ok": True, "has_data": has_data, **data})


@app.post("/api/sync")
def api_sync():
    try:
        result = sync_all()
        return jsonify(result)
    except ChromeNotOpenError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/rules")
def api_rules():
    data = load_dashboard()
    return jsonify({"ok": True, "rules": data.get("rules") or {}})


@app.get("/api/db/players")
def api_db_players():
    try:
        players = list_players()
        return jsonify({"ok": True, "count": len(players), "players": players})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/db/watchlist")
def api_db_watchlist():
    try:
        data = list_watchlist_by_position()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/db/watchlist")
def api_db_watchlist_clear():
    try:
        result = clear_watchlist()
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/players/<int:player_id>/favourite")
def api_db_player_favourite_add(player_id: int):
    try:
        player = add_favourite(player_id)
        if not player:
            return jsonify({"ok": False, "error": "Player not found."}), 404
        return jsonify({"ok": True, "player": player, "is_favourite": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/db/players/<int:player_id>/favourite")
def api_db_player_favourite_remove(player_id: int):
    try:
        player = remove_favourite(player_id)
        if not player:
            return jsonify({"ok": False, "error": "Player not found."}), 404
        return jsonify({"ok": True, "player": player, "is_favourite": False})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/db/players/<int:player_id>")
def api_db_player_detail(player_id: int):
    try:
        detail = get_player_detail(player_id)
        if not detail:
            return jsonify({"ok": False, "error": "Player not found."}), 404
        return jsonify({"ok": True, **detail})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/db/players/<int:player_id>")
def api_db_player_delete(player_id: int):
    try:
        deleted = delete_player(player_id)
        if not deleted:
            return jsonify({"ok": False, "error": "Player not found."}), 404
        return jsonify({"ok": True, "deleted": deleted})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/players/clear")
def api_db_players_clear():
    try:
        counts = clear_players_db()
        return jsonify({"ok": True, **counts})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _parse_player_ids(payload: dict) -> list[int]:
    raw = payload.get("ids") or payload.get("player_ids") or []
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _bulk_add_from_request():
    payload = request.get_json(silent=True) or {}
    try:
        limit = int(payload.get("limit") or request.args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    if limit not in {10, 50, 200}:
        if limit >= 100:
            limit = 200
        elif limit >= 30:
            limit = 50
        else:
            limit = 10

    def run(progress):
        return add_missing_players(
            limit=limit, scrape_details=True, progress_cb=progress
        )

    return jsonify(
        start_job(
            kind="bulk_add",
            label=f"Add {limit} new players (full details)",
            fn=run,
        )
    )


@app.post("/api/bulk-add-players")
@app.post("/api/db/players/bulk-add")
def api_db_players_bulk_add():
    return _bulk_add_from_request()


@app.post("/api/refresh-all-players")
@app.post("/api/db/players/refresh-all")
def api_db_players_refresh_all():
    payload = request.get_json(silent=True) or {}
    ids = _parse_player_ids(payload)

    def run(progress):
        return refresh_all_players_in_db(
            player_ids=ids or None,
            progress_cb=progress,
        )

    label = (
        f"Refresh {len(ids)} selected players"
        if ids
        else "Refresh all players"
    )
    return jsonify(start_job(kind="refresh_all", label=label, fn=run))


@app.post("/api/db/players/refresh-selected")
def api_db_players_refresh_selected():
    payload = request.get_json(silent=True) or {}
    ids = _parse_player_ids(payload)
    if not ids:
        return jsonify({"ok": False, "error": "ids is required."}), 400

    def run(progress):
        return refresh_all_players_in_db(player_ids=ids, progress_cb=progress)

    return jsonify(
        start_job(
            kind="refresh_selected",
            label=f"Refresh {len(ids)} selected players",
            fn=run,
        )
    )

@app.post("/api/db/players/delete-selected")
def api_db_players_delete_selected():
    payload = request.get_json(silent=True) or {}
    ids = _parse_player_ids(payload)
    if not ids:
        return jsonify({"ok": False, "error": "ids is required."}), 400
    try:
        result = delete_players(ids)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/players/add")
def api_db_players_add():
    payload = request.get_json(silent=True) or {}
    yahoo_id = str(payload.get("player_id") or payload.get("yahoo_id") or "").strip()
    if not yahoo_id:
        return jsonify({"ok": False, "error": "player_id is required."}), 400

    existing = get_player_by_yahoo_id(yahoo_id)
    if existing:
        return jsonify(
            {
                "ok": True,
                "already_existed": True,
                "player_id": existing["id"],
                "yahoo_id": yahoo_id,
                "full_name": existing["full_name"],
                "player": existing,
            }
        )

    scrape_details = bool(payload.get("scrape_details", True))
    raw = {
        "player_id": yahoo_id,
        "name": payload.get("name") or payload.get("full_name"),
        "positions": payload.get("positions"),
        "nba_team": payload.get("nba_team"),
        "profile_url": payload.get("profile_url"),
        "rank": payload.get("rank") or payload.get("yahoo_rank"),
        "owned_pct": payload.get("owned_pct"),
        "gp": payload.get("gp"),
        "fg_pct": payload.get("fg_pct"),
        "ft_pct": payload.get("ft_pct"),
        "threes": payload.get("threes"),
        "pts": payload.get("pts"),
        "reb": payload.get("reb"),
        "ast": payload.get("ast"),
        "stl": payload.get("stl"),
        "blk": payload.get("blk"),
        "to": payload.get("to"),
    }

    def run(progress):
        progress(0, 1, f"Adding {raw.get('name') or yahoo_id}…")
        result = add_player_to_db(raw, scrape_details=scrape_details)
        progress(1, 1, result.get("full_name") or "Done")
        return result

    return jsonify(
        start_job(
            kind="add_one",
            label=f"Add {raw.get('name') or yahoo_id}",
            fn=run,
        )
    )

@app.post("/api/db/players/<int:player_id>/refresh")
def api_db_player_refresh(player_id: int):
    def run(progress):
        progress(0, 1, "Refreshing player…")
        result = refresh_player_in_db(player_id)
        progress(1, 1, result.get("full_name") or "Done")
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Refresh failed.")
        return result

    return jsonify(
        start_job(
            kind="refresh_one",
            label=f"Refresh player #{player_id}",
            fn=run,
        )
    )

@app.get("/api/players/search")
def api_players_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": False, "error": "Query must be at least 2 characters."}), 400
    if scrape_busy():
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Yahoo scrape job is running — search is paused until it finishes.",
                    "active_job": active_job(),
                }
            ),
            409,
        )
    try:
        driver = attach_chrome()
        result = search_players(driver, q)
        known = get_yahoo_ids()
        players = []
        for p in result.get("players") or []:
            pid = str(p.get("player_id") or "")
            in_db = pid in known
            db_row = get_player_by_yahoo_id(pid) if in_db else None
            players.append(
                {
                    **p,
                    "in_db": in_db,
                    "db_id": db_row["id"] if db_row else None,
                }
            )
        return jsonify(
            {
                "ok": True,
                "query": result.get("query"),
                "count": len(players),
                "players": players,
                "source_url": result.get("source_url"),
            }
        )
    except ChromeNotOpenError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/players/top-available")
def api_players_top_available():
    try:
        limit = min(int(request.args.get("limit") or 15), 50)
    except ValueError:
        limit = 15
    try:
        driver = attach_chrome()
        result = top_available(driver, limit=limit)
        known = get_yahoo_ids()
        players = []
        for p in result.get("players") or []:
            pid = str(p.get("player_id") or "")
            in_db = pid in known
            db_row = get_player_by_yahoo_id(pid) if in_db else None
            players.append(
                {
                    **p,
                    "in_db": in_db,
                    "db_id": db_row["id"] if db_row else None,
                }
            )
        pick = result.get("smart_pick")
        if pick:
            pid = str(pick.get("player_id") or "")
            pick = {
                **pick,
                "in_db": pid in known,
                "db_id": (get_player_by_yahoo_id(pid) or {}).get("id") if pid in known else None,
            }
        return jsonify(
            {
                "ok": True,
                **{k: v for k, v in result.items() if k not in {"players", "smart_pick"}},
                "players": players,
                "smart_pick": pick,
            }
        )
    except ChromeNotOpenError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    from main import cmd_serve

    raise SystemExit(cmd_serve())
