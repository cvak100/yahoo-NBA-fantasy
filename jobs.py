"""In-memory background jobs for long Yahoo scrapes."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

# progress(current, total, message, *, steps=None, step=None, status=None, detail=None)
ProgressCb = Callable[..., None]

_lock = threading.Lock()
_scrape_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_active_job_id: str | None = None
_scrape_lock_owner: str | None = None
_active_driver: Any = None
_driver_lock = threading.Lock()

_ACTIVE_STATUSES = frozenset({"queued", "running", "paused"})
_TERMINAL_STATUSES = frozenset({"done", "error", "cancelled"})


class JobCancelled(Exception):
    """Raised inside a job when the user requests cancel."""


def _now() -> float:
    return time.time()


def bind_job_driver(driver: Any) -> None:
    """Register the Selenium driver for the active job so cancel can stop page loads."""
    global _active_driver
    with _driver_lock:
        _active_driver = driver


def unbind_job_driver(driver: Any = None) -> None:
    global _active_driver
    with _driver_lock:
        if driver is None or _active_driver is driver:
            _active_driver = None


_DEFAULT_PAGE_LOAD_TIMEOUT = 90.0


def restore_page_load_timeout(driver: Any = None) -> None:
    """Put Selenium page-load timeout back after an abort."""
    with _driver_lock:
        target = driver if driver is not None else _active_driver
    if target is None:
        return
    try:
        target.set_page_load_timeout(_DEFAULT_PAGE_LOAD_TIMEOUT)
    except Exception:
        pass


def _abort_active_driver(*, hard: bool = False) -> None:
    """Stop in-flight Selenium navigation.

    soft (pause): Page.stopLoading only — keep session healthy for Resume.
    hard (stop): briefly lower timeout + about:blank, then restore timeout.
    """
    with _driver_lock:
        driver = _active_driver
    if driver is None:
        return
    try:
        driver.execute_cdp_cmd("Page.stopLoading", {})
    except Exception:
        pass
    try:
        driver.execute_script("window.stop();")
    except Exception:
        pass
    if not hard:
        return
    try:
        driver.set_page_load_timeout(1.5)
        try:
            driver.get("about:blank")
        except Exception:
            pass
    finally:
        restore_page_load_timeout(driver)


def _find_job_unlocked(job_id: str | None = None) -> dict[str, Any] | None:
    """Find an active (queued/running/paused) job."""
    if job_id:
        job = _jobs.get(job_id)
        if job and job["status"] in _ACTIVE_STATUSES:
            return job
        return None
    if _active_job_id and _active_job_id in _jobs:
        job = _jobs[_active_job_id]
        if job["status"] in _ACTIVE_STATUSES:
            return job
    for candidate in reversed(list(_jobs.values())):
        if candidate["status"] in _ACTIVE_STATUSES:
            return candidate
    return None


def _resolve_job_unlocked(job_id: str | None = None) -> dict[str, Any] | None:
    """Resolve job by id even after it was marked cancelled."""
    if job_id:
        return _jobs.get(job_id)
    if _active_job_id and _active_job_id in _jobs:
        return _jobs[_active_job_id]
    return _find_job_unlocked(None)


def _elapsed_unlocked(job: dict[str, Any], now: float | None = None) -> float | None:
    started = job.get("started_at")
    if not started:
        return None
    now = _now() if now is None else now
    end = job.get("ended_at")
    if end is None:
        end = now
    paused_accum = float(job.get("paused_accum_sec") or 0.0)
    pause_started = job.get("pause_started_at")
    if pause_started is not None and job.get("paused"):
        paused_accum += max(0.0, float(now) - float(pause_started))
    return max(0.0, float(end) - float(started) - paused_accum)


def _is_cancel_requested_unlocked(job: dict[str, Any] | None) -> bool:
    return bool(
        job
        and (
            job.get("cancel_requested")
            or job.get("status") == "cancelled"
        )
    )


def raise_if_cancelled(job_id: str | None = None) -> None:
    """Call from scrapers between slow steps so Stop aborts quickly."""
    with _lock:
        job = _resolve_job_unlocked(job_id)
        if _is_cancel_requested_unlocked(job):
            raise JobCancelled("Cancelled by user")
    wait_while_paused(job_id)
    with _lock:
        job = _resolve_job_unlocked(job_id)
        if _is_cancel_requested_unlocked(job):
            raise JobCancelled("Cancelled by user")


def wait_while_paused(job_id: str | None = None) -> None:
    """Block while the active job is paused; still honor cancel."""
    while True:
        with _lock:
            job = _resolve_job_unlocked(job_id)
            if _is_cancel_requested_unlocked(job):
                raise JobCancelled("Cancelled by user")
            if not job or job["status"] not in _ACTIVE_STATUSES:
                return
            if not job.get("paused"):
                return
            job["message"] = "Paused — click Resume to continue"
            job["updated_at"] = _now()
        time.sleep(0.1)


def interruptible_sleep(seconds: float, *, slice_sec: float = 0.1) -> None:
    """Sleep in short slices; honor pause + cancel."""
    deadline = time.time() + max(0.0, float(seconds))
    while True:
        raise_if_cancelled()
        left = deadline - time.time()
        if left <= 0:
            return
        time.sleep(min(slice_sec, left))


def _finalize_cancelled_unlocked(job: dict[str, Any]) -> None:
    """Mark job cancelled immediately (UI); worker just winds down."""
    job["cancel_requested"] = True
    job["paused"] = False
    if job.get("pause_started_at") is not None:
        job["paused_accum_sec"] = float(job.get("paused_accum_sec") or 0.0) + max(
            0.0, _now() - float(job["pause_started_at"])
        )
        job["pause_started_at"] = None
    job["status"] = "cancelled"
    job["error"] = None
    job["message"] = "Cancelled"
    job["result"] = {"ok": False, "cancelled": True}
    for s in job.get("steps") or []:
        if s.get("status") == "running":
            s["status"] = "cancelled"
            detail = s.get("detail") or ""
            s["detail"] = f"{detail} · stopped" if detail else "Stopped"
        elif s.get("status") == "pending":
            s["status"] = "skipped"
            s["detail"] = "Skipped"
    job["ended_at"] = _now()
    job["updated_at"] = job["ended_at"]


def _public(job: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        "id": job["id"],
        "kind": job["kind"],
        "label": job["label"],
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "message": job["message"],
        "steps": list(job.get("steps") or []),
        "current_step": job.get("current_step"),
        "error": job.get("error"),
        "result": job.get("result"),
        "cancel_requested": bool(job.get("cancel_requested")),
        "paused": bool(job.get("paused")),
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
        "elapsed_sec": _elapsed_unlocked(job, now),
        "updated_at": job.get("updated_at"),
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return _public(job) if job else None


def _active_job_unlocked() -> dict[str, Any] | None:
    for job in reversed(list(_jobs.values())):
        if job["status"] in _ACTIVE_STATUSES:
            return _public(job)
    return None


def active_job() -> dict[str, Any] | None:
    with _lock:
        return _active_job_unlocked()


def scrape_busy() -> bool:
    return _scrape_lock.locked()


def _clear_lock_owner_unlocked(job_id: str | None) -> bool:
    """Clear owner markers if they match job_id (or job_id is None). Return if we should unlock."""
    global _scrape_lock_owner, _active_job_id
    if job_id and _scrape_lock_owner not in {None, job_id}:
        return False
    if _active_job_id == job_id or job_id is None:
        if _active_job_id == job_id:
            _active_job_id = None
    if _scrape_lock_owner == job_id or job_id is None or _scrape_lock_owner is None:
        _scrape_lock_owner = None
        return True
    return False


def _release_scrape_lock_for(job_id: str | None) -> None:
    """Release scrape lock if still owned by this job (safe to call twice)."""
    with _lock:
        should_unlock = _clear_lock_owner_unlocked(job_id)
    if should_unlock and _scrape_lock.locked():
        try:
            _scrape_lock.release()
        except RuntimeError:
            pass


def _acquire_scrape_lock_or_reclaim() -> tuple[bool, dict[str, Any] | None]:
    """Acquire scrape lock; reclaim if previous owner already finished/cancelled."""
    if _scrape_lock.acquire(blocking=False):
        return True, None
    with _lock:
        owner_id = _scrape_lock_owner
        owner = _jobs.get(owner_id) if owner_id else None
        owner_status = owner.get("status") if owner else None
        stale = (
            owner is None
            or owner_status in _TERMINAL_STATUSES
            or (owner is not None and owner.get("cancel_requested"))
        )
        current = _active_job_unlocked()
    if not stale:
        return False, current
    _release_scrape_lock_for(owner_id)
    if _scrape_lock.acquire(blocking=False):
        return True, None
    return False, active_job()


def cancel_job(job_id: str | None = None) -> dict[str, Any]:
    """Cancel immediately; free scrape lock so a new refresh can start right away."""
    release_id: str | None = None
    public: dict[str, Any] | None = None
    already_done = False
    err: str | None = None
    with _lock:
        job = _find_job_unlocked(job_id)
        if not job:
            maybe = _jobs.get(job_id) if job_id else None
            if maybe and maybe.get("status") == "cancelled":
                release_id = str(maybe.get("id") or "") or None
                public = _public(maybe)
                already_done = True
            elif _scrape_lock.locked() and _active_job_unlocked() is None:
                release_id = _scrape_lock_owner
                err = "No running job to cancel."
            else:
                err = "No running job to cancel."
        elif job["status"] not in _ACTIVE_STATUSES:
            # e.g. already cancelled/done — still free a stuck lock for that id
            release_id = job["id"] if job.get("status") in _TERMINAL_STATUSES else None
            err = f"Job is already {job['status']}."
            public = _public(job)
            already_done = job.get("status") == "cancelled"
        else:
            release_id = job["id"]
            _finalize_cancelled_unlocked(job)
            public = _public(job)

    if release_id is not None:
        _release_scrape_lock_for(release_id)
        unbind_job_driver()
    elif err and _scrape_lock.locked() and active_job() is None:
        _release_scrape_lock_for(_scrape_lock_owner)
        unbind_job_driver()

    if already_done and public is not None:
        return {"ok": True, "job": public}

    if err:
        payload: dict[str, Any] = {"ok": False, "error": err}
        if public is not None:
            payload["job"] = public
        return payload

    threading.Thread(
        target=lambda: _abort_active_driver(hard=True),
        daemon=True,
        name="job-abort-driver",
    ).start()
    return {"ok": True, "job": public}


def pause_job(job_id: str | None = None) -> dict[str, Any]:
    """Pause a running job at the next checkpoint."""
    with _lock:
        job = _find_job_unlocked(job_id)
        if not job:
            return {"ok": False, "error": "No running job to pause."}
        if job["status"] not in _ACTIVE_STATUSES:
            return {
                "ok": False,
                "error": f"Job is already {job['status']}.",
                "job": _public(job),
            }
        if job.get("cancel_requested"):
            return {"ok": False, "error": "Job is cancelling.", "job": _public(job)}
        if job.get("paused") or job["status"] == "paused":
            return {"ok": True, "job": _public(job)}
        job["paused"] = True
        job["status"] = "paused"
        job["pause_started_at"] = _now()
        job["message_before_pause"] = job.get("message") or ""
        job["message"] = "Pausing…"
        job["updated_at"] = _now()
        public = _public(job)
    threading.Thread(
        target=lambda: _abort_active_driver(hard=False),
        daemon=True,
        name="job-pause-driver",
    ).start()
    return {"ok": True, "job": public}


def resume_job(job_id: str | None = None) -> dict[str, Any]:
    """Resume a paused job."""
    with _lock:
        job = _find_job_unlocked(job_id)
        if not job:
            return {"ok": False, "error": "No paused job to resume."}
        if not job.get("paused") and job["status"] != "paused":
            return {
                "ok": False,
                "error": "Job is not paused.",
                "job": _public(job),
            }
        if job.get("pause_started_at") is not None:
            job["paused_accum_sec"] = float(job.get("paused_accum_sec") or 0.0) + max(
                0.0, _now() - float(job["pause_started_at"])
            )
            job["pause_started_at"] = None
        job["paused"] = False
        job["status"] = "running"
        prev = job.get("message_before_pause") or ""
        job["message"] = prev if prev and prev not in {"Pausing…", "Paused — click Resume to continue"} else "Resuming…"
        job["updated_at"] = _now()
        public = _public(job)
    restore_page_load_timeout()
    return {"ok": True, "job": public}


def start_job(
    *,
    kind: str,
    label: str,
    fn: Callable[[ProgressCb], Any],
) -> dict[str, Any]:
    """Start a Selenium-backed job. Only one scrape job at a time."""
    global _active_job_id, _scrape_lock_owner

    ok, current = _acquire_scrape_lock_or_reclaim()
    if not ok:
        return {
            "ok": False,
            "error": "Another Yahoo job is already running. Finish or wait for it.",
            "active_job": current,
        }

    job_id = uuid.uuid4().hex[:12]
    started = _now()
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "status": "running",
        "progress": 0,
        "total": 0,
        "message": "Starting…",
        "steps": [],
        "current_step": None,
        "error": None,
        "result": None,
        "cancel_requested": False,
        "paused": False,
        "pause_started_at": None,
        "paused_accum_sec": 0.0,
        "message_before_pause": "",
        "started_at": started,
        "ended_at": None,
        "updated_at": started,
    }
    with _lock:
        _jobs[job_id] = job
        _active_job_id = job_id
        _scrape_lock_owner = job_id

    def _raise_if_cancelled_unlocked() -> None:
        if job.get("cancel_requested"):
            raise JobCancelled("Cancelled by user")

    def progress(
        current: int,
        total: int,
        message: str = "",
        *,
        steps: list[dict[str, Any]] | None = None,
        step: str | None = None,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        wait_while_paused(job_id)
        with _lock:
            _raise_if_cancelled_unlocked()
            job["progress"] = int(current)
            job["total"] = int(total)
            if message and not job.get("paused"):
                job["message"] = message
            if steps is not None:
                job["steps"] = [
                    {
                        "key": str(s.get("key") or f"step-{i}"),
                        "label": str(s.get("label") or s.get("key") or f"Step {i + 1}"),
                        "status": str(s.get("status") or "pending"),
                        "detail": str(s.get("detail") or ""),
                        "avg_sec": s.get("avg_sec"),
                        "elapsed_sec": s.get("elapsed_sec"),
                    }
                    for i, s in enumerate(steps)
                ]
            if step is not None:
                job["current_step"] = step
                for src in steps or []:
                    if str(src.get("key")) != step:
                        continue
                    for s in job["steps"]:
                        if s["key"] != step:
                            continue
                        if status:
                            s["status"] = status
                        if detail is not None:
                            s["detail"] = detail
                        if "avg_sec" in src:
                            s["avg_sec"] = src.get("avg_sec")
                        if "elapsed_sec" in src:
                            s["elapsed_sec"] = src.get("elapsed_sec")
                        break
                    break
            job["updated_at"] = _now()
            _raise_if_cancelled_unlocked()

    def runner() -> None:
        try:
            result = fn(progress)
            with _lock:
                if job.get("cancel_requested") or job["status"] == "cancelled":
                    if job["status"] != "cancelled":
                        _finalize_cancelled_unlocked(job)
                    return
                job["status"] = "done"
                job["paused"] = False
                job["result"] = (
                    result if isinstance(result, dict) else {"ok": True, "data": result}
                )
                job["message"] = job.get("message") or "Done."
                if job["total"] and job["progress"] < job["total"]:
                    job["progress"] = job["total"]
                for s in job.get("steps") or []:
                    if s.get("status") in {"pending", "running"}:
                        s["status"] = "done"
                job["ended_at"] = _now()
                job["updated_at"] = job["ended_at"]
        except JobCancelled:
            with _lock:
                if job["status"] != "cancelled":
                    _finalize_cancelled_unlocked(job)
        except Exception as exc:
            with _lock:
                if job.get("cancel_requested") or job["status"] == "cancelled":
                    if job["status"] != "cancelled":
                        _finalize_cancelled_unlocked(job)
                else:
                    job["status"] = "error"
                    job["paused"] = False
                    job["error"] = str(exc)
                    job["message"] = str(exc)
                    cur = job.get("current_step")
                    for s in job.get("steps") or []:
                        if s.get("key") == cur and s.get("status") == "running":
                            s["status"] = "error"
                            s["detail"] = str(exc)
                    job["ended_at"] = _now()
                    job["updated_at"] = job["ended_at"]
        finally:
            # If cancel already released the lock for a new job, do not release again.
            with _lock:
                still_owner = _scrape_lock_owner == job_id
            if still_owner:
                unbind_job_driver()
                _release_scrape_lock_for(job_id)
            else:
                # Lock already reclaimed after Stop — just drop our driver bind if any.
                unbind_job_driver()

    threading.Thread(target=runner, daemon=True, name=f"job-{job_id}").start()
    return {"ok": True, "job_id": job_id, "job": _public(job)}
