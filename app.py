"""Flask dashboard for Yahoo Fantasy league."""

from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template, request

import config
from chrome_browser import ChromeNotOpenError, attach_chrome
from db import (
    add_favourite,
    clear_players_db,
    clear_watchlist,
    create_note,
    delete_note,
    delete_player,
    delete_players,
    fantasy_news_counts,
    get_note,
    get_player_by_yahoo_id,
    get_player_detail,
    get_yahoo_ids,
    list_fantasy_news,
    list_injuries_by_team,
    list_notes,
    list_players,
    list_players_by_team,
    list_roster_trends,
    list_watchlist_by_position,
    remove_favourite,
    sync_fantasy_news_links,
    update_note,
)
from app_settings import load_app_settings, save_app_settings
from jobs import active_job, cancel_job, get_job, pause_job, resume_job, scrape_busy, start_job
from scrapers.fantasy_news import SOURCE_CBS, SOURCE_NBA, sync_fantasy_news
from scrapers.injuries import sync_injuries
from scrapers.main_refresh import run_main_refresh
from scrapers.trends import sync_trends
from scrapers.free_players import load_free_players, sync_free_players
from scrapers.standings_detail import load_standings_detail, sync_standings_detail
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


@app.post("/api/jobs/<job_id>/cancel")
def api_jobs_cancel(job_id: str):
    result = cancel_job(job_id)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.post("/api/jobs/cancel")
def api_jobs_cancel_active():
    result = cancel_job(None)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.post("/api/jobs/<job_id>/pause")
def api_jobs_pause(job_id: str):
    result = pause_job(job_id)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.post("/api/jobs/<job_id>/resume")
def api_jobs_resume(job_id: str):
    result = resume_job(job_id)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.post("/api/jobs/pause")
def api_jobs_pause_active():
    result = pause_job(None)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.post("/api/jobs/resume")
def api_jobs_resume_active():
    result = resume_job(None)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@app.get("/")
def index():
    return render_template("index.html", league_id=config.LEAGUE_ID, active="home")


@app.get("/roster")
def roster_page():
    return render_template("roster.html", league_id=config.LEAGUE_ID, active="roster")


@app.get("/players")
def players_page():
    return render_template("search.html", league_id=config.LEAGUE_ID, active="players")


@app.get("/search")
def search_page_redirect():
    """Old /search URL → /players."""
    qs = request.query_string.decode("utf-8") if request.query_string else ""
    return redirect(f"/players?{qs}" if qs else "/players", code=301)


@app.get("/watchlist")
def watchlist_page():
    return render_template("watchlist.html", league_id=config.LEAGUE_ID, active="watchlist")


@app.get("/news")
def news_page():
    return render_template("news.html", league_id=config.LEAGUE_ID, active="news")


@app.get("/teams")
def teams_page():
    return render_template("teams.html", league_id=config.LEAGUE_ID, active="teams")


@app.get("/injuries")
def injuries_page():
    return render_template("injuries.html", league_id=config.LEAGUE_ID, active="injuries")


@app.get("/trends")
def trends_page():
    return render_template("trends.html", league_id=config.LEAGUE_ID, active="trends")


@app.get("/standings")
def standings_page():
    return render_template(
        "standings.html", league_id=config.LEAGUE_ID, active="standings"
    )


@app.get("/free-players")
def free_players_page():
    return render_template(
        "free_players.html", league_id=config.LEAGUE_ID, active="free_players"
    )


@app.get("/analitika")
@app.get("/analytics")
def analytics_page():
    return render_template(
        "analytics.html", league_id=config.LEAGUE_ID, active="analytics"
    )


@app.get("/scouting")
def scouting_page():
    return render_template(
        "scouting.html", league_id=config.LEAGUE_ID, active="scouting"
    )


@app.get("/settings")
def settings_page():
    return render_template("settings.html", league_id=config.LEAGUE_ID, active="settings")


@app.get("/api/dashboard")
def api_dashboard():
    data = load_dashboard()
    has_data = bool(data.get("meta") or data.get("my_team") or data.get("standings"))
    return jsonify({"ok": True, "has_data": has_data, **data})


@app.get("/api/roster")
def api_roster():
    from roster_view import load_my_roster_page

    week = request.args.get("week", type=int)
    return jsonify(load_my_roster_page(week=week))


@app.get("/api/roster/lockouts")
def api_roster_lockouts():
    from roster_view import load_lockouts

    week = request.args.get("week", type=int)
    try:
        return jsonify(load_lockouts(week=week))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/analytics")
def api_analytics():
    from analytics_view import load_analytics

    try:
        return jsonify(load_analytics())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/scouting")
def api_scouting():
    from opponent_scouting import build_scouting

    team_id = (request.args.get("team_id") or "").strip() or None
    try:
        return jsonify(build_scouting(team_id))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/scouting/<team_id>/refresh")
def api_scouting_refresh(team_id: str):
    from opponent_scouting import sync_opponent_roster

    tid = str(team_id).strip()

    def run(progress):
        return sync_opponent_roster(tid, progress_cb=progress)

    return jsonify(
        start_job(
            kind="opponent_scouting",
            label=f"Scouting roster {tid}",
            fn=run,
        )
    )


@app.get("/api/notes")
def api_notes_list():
    try:
        return jsonify({"ok": True, "notes": list_notes()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/notes/link-options")
def api_notes_link_options():
    try:
        from nba_teams import all_teams
        from opponent_scouting import list_fantasy_teams

        players = [
            {
                "id": str(p["id"]),
                "label": p.get("full_name") or f"Player {p['id']}",
            }
            for p in list_players()
        ]
        nba = [
            {"id": t["abbr"], "label": f"{t['abbr']} · {t['name']}"}
            for t in all_teams()
        ]
        fantasy = [
            {
                "id": str(t["team_id"]),
                "label": t.get("name") or f"Team {t['team_id']}",
            }
            for t in list_fantasy_teams()
        ]
        return jsonify(
            {
                "ok": True,
                "players": players,
                "nba_teams": nba,
                "fantasy_teams": fantasy,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/notes")
def api_notes_create():
    payload = request.get_json(silent=True) or {}
    try:
        note = create_note(
            title=payload.get("title"),
            body=payload.get("body") or "",
            links=payload.get("links"),
        )
        return jsonify({"ok": True, "note": note})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/notes/<int:note_id>")
def api_notes_get(note_id: int):
    note = get_note(note_id)
    if not note:
        return jsonify({"ok": False, "error": "Note not found."}), 404
    return jsonify({"ok": True, "note": note})


@app.put("/api/notes/<int:note_id>")
@app.post("/api/notes/<int:note_id>")
def api_notes_update(note_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        note = update_note(
            note_id,
            title=payload.get("title"),
            body=payload.get("body"),
            links=payload.get("links"),
        )
        if not note:
            return jsonify({"ok": False, "error": "Note not found."}), 404
        return jsonify({"ok": True, "note": note})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/notes/<int:note_id>")
def api_notes_delete(note_id: int):
    try:
        ok = delete_note(note_id)
        if not ok:
            return jsonify({"ok": False, "error": "Note not found."}), 404
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/app-settings")
def api_app_settings_get():
    return jsonify({"ok": True, "settings": load_app_settings()})


@app.put("/api/app-settings")
@app.post("/api/app-settings")
def api_app_settings_save():
    payload = request.get_json(silent=True) or {}
    try:
        settings = save_app_settings(payload)
        dashboard = None
        if "my_team_id" in (payload or {}):
            from scrapers.sync import apply_my_team_selection

            dashboard = apply_my_team_selection(persist=True)
        return jsonify({"ok": True, "settings": settings, "dashboard": dashboard})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/sync")
@app.post("/api/refresh")
def api_sync():
    """Main morning refresh: league + roster + available + news/injuries/trends."""

    def run(progress):
        return run_main_refresh(progress_cb=progress)

    return jsonify(
        start_job(
            kind="main_refresh",
            label="Main refresh",
            fn=run,
        )
    )


@app.post("/api/sync/league")
def api_sync_league_only():
    """Legacy league-only sync (settings/standings/matchups)."""
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
        from fantasy_ownership import enrich_players_with_ownership

        players = enrich_players_with_ownership(list_players())
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


@app.get("/api/db/teams")
def api_db_teams():
    try:
        data = list_players_by_team()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/db/news")
def api_db_news():
    try:
        limit = int(request.args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    matched_only = str(request.args.get("matched") or "").lower() in {"1", "true", "yes"}
    unmatched_only = str(request.args.get("unmatched") or "").lower() in {"1", "true", "yes"}
    match = (request.args.get("match") or "").strip().lower() or None
    sources_raw = request.args.get("sources") or request.args.get("source") or ""
    sources = [s.strip() for s in sources_raw.split(",") if s.strip()] or None
    try:
        items = list_fantasy_news(
            limit=limit,
            matched_only=matched_only,
            unmatched_only=unmatched_only,
            match=match,
            sources=sources,
        )
        counts = fantasy_news_counts()
        known_sources = counts.get("sources") or []
        for src in (SOURCE_NBA, SOURCE_CBS):
            if src not in known_sources:
                known_sources.append(src)
        return jsonify(
            {
                "ok": True,
                "news": items,
                "count": len(items),
                "available_sources": known_sources,
                **counts,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/news/refresh")
def api_db_news_refresh():
    body = request.get_json(silent=True) or {}
    include_nba = body.get("nba", True)
    include_cbs = body.get("cbs", True)
    if isinstance(include_nba, str):
        include_nba = include_nba.lower() in {"1", "true", "yes"}
    if isinstance(include_cbs, str):
        include_cbs = include_cbs.lower() in {"1", "true", "yes"}

    def run(progress):
        return sync_fantasy_news(
            progress_cb=progress,
            include_nba=bool(include_nba),
            include_cbs=bool(include_cbs),
        )

    return jsonify(
        start_job(
            kind="fantasy_news",
            label="Refresh Fantasy News",
            fn=run,
        )
    )


@app.post("/api/db/news/sync")
@app.post("/api/db/news/relink")
def api_db_news_sync():
    """Re-match unmatched news rows to players currently in the DB."""
    try:
        result = sync_fantasy_news_links()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/db/injuries")
def api_db_injuries():
    try:
        data = list_injuries_by_team()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/injuries/refresh")
def api_db_injuries_refresh():
    def run(progress):
        return sync_injuries(progress_cb=progress)

    return jsonify(
        start_job(
            kind="injuries",
            label="Refresh Injuries",
            fn=run,
        )
    )


@app.get("/api/db/trends")
def api_db_trends():
    try:
        data = list_roster_trends()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/db/trends/refresh")
def api_db_trends_refresh():
    def run(progress):
        return sync_trends(progress_cb=progress)

    return jsonify(
        start_job(
            kind="roster_trends",
            label="Refresh Trends",
            fn=run,
        )
    )


@app.get("/api/standings-detail")
def api_standings_detail():
    try:
        data = load_standings_detail()
        return jsonify({"ok": True, **data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/standings-detail/refresh")
def api_standings_detail_refresh():
    def run(progress):
        return sync_standings_detail(progress_cb=progress)

    return jsonify(
        start_job(
            kind="standings_detail",
            label="Refresh Standings",
            fn=run,
        )
    )


@app.get("/api/free-players")
def api_free_players():
    try:
        data = load_free_players()
        # Page table shows top picks; keep payload light.
        return jsonify(
            {
                "ok": True,
                "scraped_at": data.get("scraped_at"),
                "pool": data.get("pool"),
                "top": data.get("top"),
                "stat1": data.get("stat1"),
                "weights": data.get("weights"),
                "note": data.get("note"),
                "players": data.get("players") or [],
                "count": data.get("count") or 0,
                "pool_count": data.get("pool_count") or 0,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/free-players/refresh")
def api_free_players_refresh():
    def run(progress):
        return sync_free_players(progress_cb=progress)

    return jsonify(
        start_job(
            kind="free_players",
            label="Free Players analysis",
            fn=run,
        )
    )


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
    raw_limit = payload.get("limit", request.args.get("limit", 10))
    all_players = False
    if isinstance(raw_limit, str) and raw_limit.strip().lower() in {"all", "*"}:
        all_players = True
        limit = None
    elif payload.get("all") in {True, 1, "1", "true", "yes"}:
        all_players = True
        limit = None
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 10
        if limit not in {10, 50, 200}:
            if limit >= 500:
                all_players = True
                limit = None
            elif limit >= 100:
                limit = 200
            elif limit >= 30:
                limit = 50
            else:
                limit = 10

    def run(progress):
        return add_missing_players(
            limit=limit,
            all_players=all_players,
            scrape_details=True,
            progress_cb=progress,
        )

    label = (
        "Add all missing Yahoo players (full details)"
        if all_players
        else f"Add {limit} new players (full details)"
    )
    return jsonify(
        start_job(
            kind="bulk_add",
            label=label,
            fn=run,
        )
    )


@app.post("/api/bulk-add-players")
@app.post("/api/db/players/bulk-add")
def api_db_players_bulk_add():
    return _bulk_add_from_request()


@app.post("/api/db/ownership/refresh")
def api_db_ownership_refresh():
    """Re-scrape Yahoo player Status → fantasy_owner (Players Owner column)."""

    def run(progress):
        from chrome_browser import attach_chrome
        from fantasy_ownership import sync_ownership_from_yahoo_list

        progress(0, 1, "Attaching Chrome…")
        driver = attach_chrome()
        return sync_ownership_from_yahoo_list(driver, limit=900, progress_cb=progress)

    return jsonify(
        start_job(kind="ownership_refresh", label="Update fantasy owners", fn=run)
    )


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
