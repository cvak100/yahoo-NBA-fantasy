"""In-memory background jobs for long Yahoo scrapes."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

ProgressCb = Callable[[int, int, str], None]

_lock = threading.Lock()
_scrape_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "kind": job["kind"],
        "label": job["label"],
        "status": job["status"],
        "progress": job["progress"],
        "total": job["total"],
        "message": job["message"],
        "error": job.get("error"),
        "result": job.get("result"),
        "updated_at": job.get("updated_at"),
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return _public(job) if job else None


def active_job() -> dict[str, Any] | None:
    with _lock:
        for job in reversed(list(_jobs.values())):
            if job["status"] in {"queued", "running"}:
                return _public(job)
        return None


def scrape_busy() -> bool:
    return _scrape_lock.locked()


def start_job(
    *,
    kind: str,
    label: str,
    fn: Callable[[ProgressCb], Any],
) -> dict[str, Any]:
    """Start a Selenium-backed job. Only one scrape job at a time."""
    if not _scrape_lock.acquire(blocking=False):
        current = active_job()
        return {
            "ok": False,
            "error": "Another Yahoo job is already running. Finish or wait for it.",
            "active_job": current,
        }

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "label": label,
        "status": "running",
        "progress": 0,
        "total": 0,
        "message": "Starting…",
        "error": None,
        "result": None,
        "updated_at": _now(),
    }
    with _lock:
        _jobs[job_id] = job

    def progress(current: int, total: int, message: str = "") -> None:
        with _lock:
            job["progress"] = int(current)
            job["total"] = int(total)
            if message:
                job["message"] = message
            job["updated_at"] = _now()

    def runner() -> None:
        try:
            result = fn(progress)
            with _lock:
                job["status"] = "done"
                job["result"] = result if isinstance(result, dict) else {"ok": True, "data": result}
                job["message"] = job.get("message") or "Done."
                if job["total"] and job["progress"] < job["total"]:
                    job["progress"] = job["total"]
                job["updated_at"] = _now()
        except Exception as exc:
            with _lock:
                job["status"] = "error"
                job["error"] = str(exc)
                job["message"] = str(exc)
                job["updated_at"] = _now()
        finally:
            _scrape_lock.release()

    threading.Thread(target=runner, daemon=True, name=f"job-{job_id}").start()
    return {"ok": True, "job_id": job_id, "job": _public(job)}
