"""JSON data store for scraped league snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


def _ensure_dir() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    _ensure_dir()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_dashboard() -> dict[str, Any]:
    # Apply Settings → My team on top of last scrape so UI stays in sync.
    try:
        from scrapers.sync import apply_my_team_selection

        return apply_my_team_selection(persist=True)
    except Exception:
        from roster_view import enrich_my_team_db_ids

        rules = load_json(config.RULES_PATH, {})
        standings = load_json(config.STANDINGS_PATH, {})
        my_team = load_json(config.MY_TEAM_PATH, {})
        matchups = load_json(config.MATCHUPS_PATH, {})
        meta = load_json(config.META_PATH, {})
        return {
            "meta": meta,
            "rules": rules,
            "standings": standings,
            "my_team": enrich_my_team_db_ids(my_team or {}),
            "matchups": matchups,
        }
