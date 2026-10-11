"""Main daily refresh: league + roster + news/injuries/trends + top available last."""

from __future__ import annotations

import time
from typing import Any, Callable

from selenium.webdriver.remote.webdriver import WebDriver

import config
from app_settings import load_app_settings, refresh_step_enabled, resolve_my_team_id
from chrome_browser import attach_chrome
from db import get_player_by_yahoo_id, get_yahoo_ids
from jobs import (
    JobCancelled,
    bind_job_driver,
    interruptible_sleep,
    raise_if_cancelled,
    restore_page_load_timeout,
)
from refresh_timings import avg_for_steps, avg_total_sec, record_refresh_run
from scrapers.fantasy_news import sync_fantasy_news
from scrapers.injuries import sync_injuries
from scrapers.player_db import ingest_player_from_raw, _nba_team_to_eteam, _player_row_to_raw
from scrapers.players import ranked_players
from scrapers.sync import sync_all
from scrapers.trends import sync_trends
from store import load_json, save_json, utc_now_iso

ProgressCb = Callable[..., None]

MAIN_STEPS = [
    {"key": "league", "label": "League data (settings, standings, matchups)"},
    {"key": "roster", "label": "My roster players"},
    {"key": "news", "label": "Fantasy news (NBA + CBS)"},
    {"key": "injuries", "label": "Injury report"},
    {"key": "trends", "label": "Roster / transaction trends"},
    {"key": "available", "label": "Top available players"},
]


def _fmt_sec(sec: float | None) -> str:
    if sec is None:
        return ""
    s = max(0, int(round(float(sec))))
    m, r = divmod(s, 60)
    return f"{m}:{r:02d}"


class _StepTracker:
    def __init__(
        self,
        progress_cb: ProgressCb | None,
        *,
        enabled_keys: set[str] | None = None,
    ):
        self.progress_cb = progress_cb
        self.enabled_keys = enabled_keys or {s["key"] for s in MAIN_STEPS}
        self.avgs = avg_for_steps()
        # ETA only from enabled steps.
        self.avg_total = None
        enabled_avgs = [
            float(self.avgs[k])
            for k in (s["key"] for s in MAIN_STEPS if s["key"] in self.enabled_keys)
            if k in self.avgs and self.avgs[k] is not None
        ]
        if enabled_avgs:
            self.avg_total = round(sum(enabled_avgs), 1)
        elif avg_total_sec() is not None and len(self.enabled_keys) == len(MAIN_STEPS):
            self.avg_total = avg_total_sec()
        self.step_seconds: dict[str, float] = {}
        self._started: dict[str, float] = {}
        self.run_started = time.time()
        self.steps = [
            {
                **s,
                "status": "pending",
                "detail": "",
                "avg_sec": self.avgs.get(s["key"]),
                "elapsed_sec": None,
            }
            for s in MAIN_STEPS
            if s["key"] in self.enabled_keys
        ]
        eta = _fmt_sec(self.avg_total)
        msg = (
            f"Starting main refresh… · avg {_fmt_sec(self.avg_total)}"
            if eta
            else "Starting main refresh…"
        )
        self._emit(0, msg)

    def _emit(
        self,
        index: int,
        message: str,
        *,
        step: str | None = None,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        if not self.progress_cb:
            return
        remaining = self._eta_remaining_sec()
        meta = ""
        if remaining is not None:
            meta = f" · ETA ~{_fmt_sec(remaining)}"
        self.progress_cb(
            index,
            len(self.steps),
            f"{message}{meta}" if meta and "ETA" not in message else message,
            steps=self.steps,
            step=step,
            status=status,
            detail=detail,
        )

    def _eta_remaining_sec(self) -> float | None:
        total = 0.0
        have = False
        now = time.time()
        for s in self.steps:
            key = s["key"]
            avg = s.get("avg_sec")
            status = s.get("status")
            if status == "done":
                continue
            if status == "running":
                have = True
                started = self._started.get(key)
                if avg is not None and started is not None:
                    left = max(0.0, float(avg) - (now - started))
                    total += left
                elif avg is not None:
                    total += float(avg)
                continue
            if avg is not None:
                have = True
                total += float(avg)
        return round(total, 1) if have else None

    def begin(self, key: str, message: str | None = None) -> None:
        for s in self.steps:
            if s["status"] == "running":
                s["status"] = "done"
            if s["key"] == key:
                s["status"] = "running"
                s["detail"] = ""
                s["elapsed_sec"] = None
        self._started[key] = time.time()
        idx = next(i for i, s in enumerate(self.steps) if s["key"] == key)
        label = next(s["label"] for s in self.steps if s["key"] == key)
        avg = self.avgs.get(key)
        base = message or label
        if avg is not None:
            base = f"{base} · avg ~{_fmt_sec(avg)}"
        self._emit(idx, base, step=key, status="running")

    def detail(self, key: str, message: str, *, current: int | None = None) -> None:
        idx = next(i for i, s in enumerate(self.steps) if s["key"] == key)
        for s in self.steps:
            if s["key"] == key:
                s["detail"] = message
                started = self._started.get(key)
                if started is not None:
                    s["elapsed_sec"] = round(time.time() - started, 1)
        base = idx
        if current is not None:
            frac = min(0.95, max(0.0, current / 100.0))
            base = idx + frac
        self._emit(
            int(base) if current is None else idx,
            message,
            step=key,
            status="running",
            detail=message,
        )

    def done(self, key: str, detail: str = "") -> None:
        started = self._started.pop(key, None)
        elapsed = round(time.time() - started, 1) if started is not None else None
        if elapsed is not None:
            self.step_seconds[key] = elapsed
        idx = next(i for i, s in enumerate(self.steps) if s["key"] == key)
        for s in self.steps:
            if s["key"] == key:
                s["status"] = "done"
                s["elapsed_sec"] = elapsed
                timed = f"{detail} · {_fmt_sec(elapsed)}" if detail and elapsed is not None else (
                    detail or (_fmt_sec(elapsed) if elapsed is not None else "Done")
                )
                s["detail"] = timed
        self._emit(idx + 1, detail or "Done", step=key, status="done", detail=self.steps[idx]["detail"])

    def fail(self, key: str, error: str) -> None:
        started = self._started.pop(key, None)
        elapsed = round(time.time() - started, 1) if started is not None else None
        if elapsed is not None:
            self.step_seconds[key] = elapsed
        for s in self.steps:
            if s["key"] == key:
                s["status"] = "error"
                s["elapsed_sec"] = elapsed
                s["detail"] = error
        idx = next(i for i, s in enumerate(self.steps) if s["key"] == key)
        self._emit(idx, error, step=key, status="error", detail=error)


def scrape_my_roster_players(driver: WebDriver, team_id: str | None) -> list[dict[str, Any]]:
    """Parse Yahoo My Team page for rostered players (empty before draft)."""
    if not team_id:
        return []
    url = f"{config.LEAGUE_URL}/{team_id}"
    raise_if_cancelled()
    driver.get(url)
    interruptible_sleep(max(2.0, float(getattr(config, "PAGE_LOAD_WAIT_SEC", 4)) * 0.6))
    rows = driver.execute_script(
        """
        const out = [];
        const seen = new Set();
        const posRe = /^(PG|SG|G|SF|PF|F|C|Util|UTIL|BN|IL\\+?|IR|IL)$/i;
        const tables = [...document.querySelectorAll('table')];
        // Prefer the main roster table (has Pos + player links).
        const table = tables.find((t) => {
          const heads = [...t.querySelectorAll('th')].map((th) => (th.textContent || '').trim().toLowerCase());
          return heads.some((h) => h === 'pos' || h === 'player') && t.querySelector('a.name, .ysf-player-name a');
        }) || tables.find((t) => t.querySelector('a.name, .ysf-player-name a')) || null;
        const trs = table
          ? [...table.querySelectorAll('tbody tr')]
          : [...document.querySelectorAll('table tbody tr')];
        for (const tr of trs) {
          const nameEl = tr.querySelector('a.name, .ysf-player-name a.name, .ysf-player-name a');
          const tds = [...tr.querySelectorAll('td')];
          let slotPos = '';
          const posTd = tr.querySelector('td.pos, .pos, td[class*="pos"]');
          if (posTd) slotPos = (posTd.textContent || '').trim();
          if (!slotPos && tds.length) {
            const first = (tds[0].textContent || '').trim();
            if (posRe.test(first)) slotPos = first;
          }
          if (slotPos) slotPos = slotPos.replace(/\\s+/g, '');
          if (slotPos.toUpperCase() === 'UTIL') slotPos = 'Util';

          if (!nameEl) {
            // Empty roster slot — keep placeholder so slot order stays aligned.
            if (slotPos) {
              out.push({
                player_id: '',
                yahoo_id: '',
                name: '',
                nba_team: '',
                positions: '',
                profile_url: null,
                slot_position: slotPos,
                empty: true,
              });
            }
            continue;
          }
          const name = (nameEl.textContent || '').trim();
          if (!name || /^empty$/i.test(name)) {
            if (slotPos) {
              out.push({
                player_id: '',
                yahoo_id: '',
                name: '',
                nba_team: '',
                positions: '',
                profile_url: null,
                slot_position: slotPos,
                empty: true,
              });
            }
            continue;
          }
          const href = nameEl.getAttribute('href') || '';
          const ys = nameEl.getAttribute('data-ys-playerid') || '';
          const watch = tr.querySelector('a[name^="w-"]');
          const wn = watch ? (watch.getAttribute('name') || '') : '';
          const wm = wn.match(/w-\\d+-(\\d+)/);
          const hm = href.match(/\\/players\\/(\\d+)/);
          const pid = (wm && wm[1]) || ys || (hm && hm[1]) || '';
          if (pid && seen.has(pid)) continue;
          if (pid) seen.add(pid);
          const metaEl = [...tr.querySelectorAll('.Fz-xxs, span')].find((el) => {
            const t = (el.textContent || '').trim();
            return t.includes(' - ') && t.length < 40;
          });
          const meta = metaEl ? (metaEl.textContent || '').trim() : '';
          let team = '', positions = '';
          if (meta.includes(' - ')) {
            const parts = meta.split(' - ');
            team = (parts[0] || '').trim();
            positions = parts.slice(1).join(' - ').trim();
          }
          out.push({
            player_id: pid,
            yahoo_id: pid,
            name,
            nba_team: team,
            positions,
            profile_url: href || null,
            slot_position: slotPos || '',
            empty: false,
          });
        }
        return out;
        """
    )
    return list(rows or [])


def apply_roster_to_my_team(roster_rows: list[dict[str, Any]], *, team_id: str | None) -> dict[str, Any]:
    """Write scraped roster slots into data/my_team.json for the dashboard card."""
    my_team = load_json(config.MY_TEAM_PATH, {}) or {}
    rules = load_json(config.RULES_PATH, {}) or {}
    base_slots = list(my_team.get("slots") or [])
    if not base_slots:
        for slot in rules.get("roster_positions") or []:
            base_slots.append({**slot, "player": None})

    # Reset players, keep slot definitions.
    slots = [{**s, "player": None} for s in base_slots]
    # Index of next open slot per fantasy position; empties reserve a slot.
    next_idx: dict[str, int] = {}
    reserved: set[int] = set()

    def _find_slot(pos: str) -> int | None:
        key = pos or ""
        start = next_idx.get(key, 0)
        for i in range(start, len(slots)):
            if i in reserved:
                continue
            if str(slots[i].get("position") or "") == key and slots[i].get("player") is None:
                next_idx[key] = i + 1
                return i
        return None

    filled = 0
    for row in roster_rows:
        pos = str(row.get("slot_position") or "")
        if row.get("empty") or not (row.get("name") or row.get("player_id")):
            # Keep this fantasy slot empty and do not reuse it for later players.
            if pos:
                idx = _find_slot(pos)
                if idx is not None:
                    reserved.add(idx)
            continue
        idx = _find_slot(pos) if pos else None
        if idx is None:
            # Fallback: first still-empty, non-reserved slot.
            for i, s in enumerate(slots):
                if i in reserved:
                    continue
                if s.get("player") is None:
                    idx = i
                    break
        if idx is None:
            continue
        reserved.add(idx)
        slots[idx]["player"] = {
            "yahoo_id": str(row.get("player_id") or row.get("yahoo_id") or "") or None,
            "name": row.get("name"),
            "team": row.get("nba_team") or "",
            "positions": row.get("positions") or "",
            "profile_url": row.get("profile_url"),
        }
        filled += 1

    my_team = {
        **my_team,
        "team_id": str(team_id or my_team.get("team_id") or "") or my_team.get("team_id"),
        "slots": slots,
        "filled": filled,
        "capacity": len(slots),
        "synced_at": utc_now_iso(),
    }
    if filled:
        my_team.pop("note", None)
    else:
        my_team["note"] = my_team.get("note") or (
            "Roster slots from league settings. Players appear after the draft."
        )
    save_json(config.MY_TEAM_PATH, my_team)
    return my_team


def _upsert_player_list(
    driver: WebDriver,
    raws: list[dict[str, Any]],
    *,
    team_map: dict[str, str],
    step_key: str,
    tracker: _StepTracker,
) -> dict[str, Any]:
    scraped_at = utc_now_iso()
    added = 0
    refreshed = 0
    failed: list[dict[str, str]] = []
    known = get_yahoo_ids()

    for i, raw in enumerate(raws, start=1):
        raise_if_cancelled()
        name = raw.get("name") or raw.get("full_name") or raw.get("player_id") or "?"
        yahoo_id = str(raw.get("player_id") or raw.get("yahoo_id") or "")
        tracker.detail(step_key, f"{i}/{len(raws)} · {name}", current=i)
        try:
            existing = get_player_by_yahoo_id(yahoo_id) if yahoo_id else None
            if existing:
                payload = _player_row_to_raw(existing)
                if raw.get("nba_team"):
                    payload["nba_team"] = raw.get("nba_team")
                if raw.get("positions"):
                    payload["positions"] = raw.get("positions")
                ingest_player_from_raw(
                    driver,
                    payload,
                    team_map=team_map,
                    scrape_details=True,
                    scraped_at=scraped_at,
                )
                refreshed += 1
            else:
                ingest_player_from_raw(
                    driver,
                    raw,
                    team_map=team_map,
                    scrape_details=True,
                    scraped_at=scraped_at,
                )
                added += 1
                if yahoo_id:
                    known.add(yahoo_id)
        except JobCancelled:
            raise
        except Exception as exc:
            failed.append({"name": str(name), "error": str(exc)})
            print(f"  failed {name}: {exc}", flush=True)

    return {
        "count": len(raws),
        "added": added,
        "refreshed": refreshed,
        "failed_count": len(failed),
        "failed": failed[:15],
    }


def run_main_refresh(*, progress_cb: ProgressCb | None = None) -> dict[str, Any]:
    """Full morning refresh pipeline with step-by-step progress."""
    settings = load_app_settings()
    available_n = int(settings.get("refresh_available_count") or config.DEFAULT_REFRESH_AVAILABLE_COUNT)
    enabled = {
        s["key"]
        for s in MAIN_STEPS
        if refresh_step_enabled(settings, s["key"])
    }
    tracker = _StepTracker(progress_cb, enabled_keys=enabled)
    result: dict[str, Any] = {
        "ok": True,
        "scraped_at": utc_now_iso(),
        "settings": {
            "refresh_available_count": available_n,
            "refresh_steps": dict(settings.get("refresh_steps") or {}),
            "include_my_roster": bool((settings.get("refresh_steps") or {}).get("roster", True)),
        },
        "steps": {},
        "timing": {
            "avg_total_sec": tracker.avg_total,
            "avg_steps": dict(tracker.avgs),
            "sample_count": None,
        },
    }

    if not enabled:
        if progress_cb:
            progress_cb(0, 0, "Nothing enabled in Settings → Main refresh", steps=[])
        result["note"] = "No refresh steps enabled"
        return result

    league_result: dict[str, Any] = {}
    driver = None

    def ensure_driver() -> WebDriver:
        nonlocal driver
        if driver is None:
            driver = attach_chrome()
            restore_page_load_timeout(driver)
            bind_job_driver(driver)
        return driver

    # 1) League snapshot
    if "league" in enabled:
        tracker.begin("league", "Syncing league settings, standings, matchups…")
        try:
            league_result = sync_all()
            result["steps"]["league"] = {
                "ok": True,
                "week": (league_result.get("matchups") or {}).get("week"),
                "teams": (league_result.get("standings") or {}).get("teams_count"),
            }
            tracker.done(
                "league",
                f"Week {(league_result.get('matchups') or {}).get('week') or '—'} · "
                f"{(league_result.get('standings') or {}).get('teams_count') or 0} teams",
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracker.fail("league", str(exc))
            raise
    else:
        result["steps"]["league"] = {"ok": True, "skipped": True}

    # 2) My roster
    if "roster" in enabled:
        tracker.begin("roster", "Loading my roster…")
        my_team = load_json(config.MY_TEAM_PATH, {}) or {}
        team_id = resolve_my_team_id(
            my_team.get("team_id") or (league_result.get("my_team") or {}).get("team_id")
        )
        roster_raws = scrape_my_roster_players(
            ensure_driver(), str(team_id) if team_id else None
        )
        # Always write dashboard My Team slots from the team page (incl. empties).
        apply_roster_to_my_team(roster_raws, team_id=str(team_id) if team_id else None)
        players_only = [
            r
            for r in roster_raws
            if not r.get("empty") and (r.get("player_id") or r.get("name"))
        ]
        if not players_only:
            result["steps"]["roster"] = {
                "ok": True,
                "count": 0,
                "note": "No rostered players yet",
            }
            tracker.done("roster", "No rostered players yet (pre-draft or empty)")
        else:
            tracker.detail("roster", f"Refreshing {len(players_only)} roster players…")
            team_map = _nba_team_to_eteam(ensure_driver())
            roster_stats = _upsert_player_list(
                ensure_driver(),
                players_only,
                team_map=team_map,
                step_key="roster",
                tracker=tracker,
            )
            # Re-apply after upsert so names/ids stay aligned with DB.
            apply_roster_to_my_team(
                roster_raws, team_id=str(team_id) if team_id else None
            )
            result["steps"]["roster"] = {"ok": True, **roster_stats}
            tracker.done(
                "roster",
                f"{roster_stats['refreshed']} refreshed · {roster_stats['added']} added"
                + (
                    f" · {roster_stats['failed_count']} failed"
                    if roster_stats["failed_count"]
                    else ""
                ),
            )
    else:
        result["steps"]["roster"] = {"ok": True, "skipped": True, "count": 0}

    # 3) News
    if "news" in enabled:
        tracker.begin("news", "Refreshing NBA + CBS fantasy news…")

        def news_progress(cur, total, msg=""):
            tracker.detail("news", msg or f"News {cur}/{total}")

        try:
            news_result = sync_fantasy_news(progress_cb=news_progress)
            result["steps"]["news"] = {
                "ok": True,
                "fetched": news_result.get("fetched"),
                "inserted": news_result.get("inserted"),
                "matched": news_result.get("matched"),
            }
            tracker.done(
                "news",
                f"{news_result.get('inserted', 0)} new · {news_result.get('fetched', 0)} fetched",
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracker.fail("news", str(exc))
            raise
    else:
        result["steps"]["news"] = {"ok": True, "skipped": True}

    # 4) Injuries
    if "injuries" in enabled:
        tracker.begin("injuries", "Refreshing CBS injury report…")

        def inj_progress(cur, total, msg=""):
            tracker.detail("injuries", msg or f"Injuries {cur}/{total}")

        try:
            inj_result = sync_injuries(progress_cb=inj_progress)
            result["steps"]["injuries"] = {
                "ok": True,
                "inserted": inj_result.get("inserted"),
                "team_count": inj_result.get("team_count"),
            }
            tracker.done(
                "injuries",
                f"{inj_result.get('inserted', 0)} players · {inj_result.get('team_count', 0)} teams",
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracker.fail("injuries", str(exc))
            raise
    else:
        result["steps"]["injuries"] = {"ok": True, "skipped": True}

    # 5) Trends
    if "trends" in enabled:
        tracker.begin("trends", "Refreshing CBS + Yahoo trends…")

        def trends_progress(cur, total, msg=""):
            tracker.detail("trends", msg or f"Trends {cur}/{total}")

        try:
            trends_result = sync_trends(progress_cb=trends_progress)
            result["steps"]["trends"] = {
                "ok": True,
                "inserted": trends_result.get("inserted"),
                "by_source": trends_result.get("by_source"),
            }
            tracker.done(
                "trends",
                f"{trends_result.get('inserted', 0)} rows saved",
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracker.fail("trends", str(exc))
            raise
    else:
        result["steps"]["trends"] = {"ok": True, "skipped": True}

    # 6) Top available (last — slowest)
    if "available" in enabled:
        tracker.begin("available", f"Loading top {available_n} available players…")
        try:
            available_raws = ranked_players(
                ensure_driver(), limit=available_n, status="A", sort="AR"
            )
            tracker.detail(
                "available",
                f"Checking {len(available_raws)} available players…",
            )
            team_map = _nba_team_to_eteam(ensure_driver())
            avail_stats = _upsert_player_list(
                ensure_driver(),
                available_raws,
                team_map=team_map,
                step_key="available",
                tracker=tracker,
            )
            result["steps"]["available"] = {"ok": True, **avail_stats}
            tracker.done(
                "available",
                f"{avail_stats['refreshed']} refreshed · {avail_stats['added']} added"
                + (
                    f" · {avail_stats['failed_count']} failed"
                    if avail_stats["failed_count"]
                    else ""
                ),
            )
        except JobCancelled:
            raise
        except Exception as exc:
            tracker.fail("available", str(exc))
            raise
    else:
        result["steps"]["available"] = {"ok": True, "skipped": True}

    total_sec = round(time.time() - tracker.run_started, 2)
    timing_payload = record_refresh_run(
        step_seconds=tracker.step_seconds,
        total_sec=total_sec,
    )
    result["timing"] = {
        "total_sec": total_sec,
        "steps": dict(tracker.step_seconds),
        "avg_total_sec": (timing_payload.get("avg") or {}).get("total_sec"),
        "avg_steps": (timing_payload.get("avg") or {}).get("steps") or {},
        "sample_count": (timing_payload.get("avg") or {}).get("sample_count"),
    }

    step_count = len(tracker.steps) or 1
    if progress_cb:
        progress_cb(
            step_count,
            step_count,
            f"Main refresh complete · {_fmt_sec(total_sec)}"
            + (
                f" (avg {_fmt_sec(result['timing']['avg_total_sec'])})"
                if result["timing"].get("avg_total_sec") is not None
                else ""
            ),
            steps=tracker.steps,
        )
    result["steps_ui"] = tracker.steps
    return result
