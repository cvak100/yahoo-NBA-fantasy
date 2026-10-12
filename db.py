"""SQLite player database — upsert with content-hash dedupe."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    yahoo_id TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    positions TEXT,
    nba_team TEXT,
    profile_url TEXT,
    yahoo_rank INTEGER,
    owned_pct TEXT,
    fantasy_owner TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    record_type TEXT NOT NULL,
    published_at TEXT,
    source TEXT,
    title TEXT,
    body TEXT,
    game_date TEXT,
    opponent TEXT,
    is_home INTEGER,
    status TEXT,
    minutes TEXT,
    fgm_a TEXT,
    fg_pct TEXT,
    ftm_a TEXT,
    ft_pct TEXT,
    threes TEXT,
    pts TEXT,
    reb TEXT,
    ast TEXT,
    stl TEXT,
    blk TEXT,
    tov TEXT,
    season TEXT,
    games_played TEXT,
    season_rank TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_depth_chart (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    nba_team TEXT NOT NULL,
    position TEXT NOT NULL,
    depth_rank INTEGER NOT NULL,
    depth_player_name TEXT NOT NULL,
    depth_yahoo_id TEXT,
    yahoo_rank INTEGER,
    content_hash TEXT NOT NULL UNIQUE,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fantasy_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    player_name TEXT,
    player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    nba_player_id TEXT,
    published_at TEXT,
    published_iso TEXT,
    source TEXT,
    title TEXT,
    body TEXT,
    team_name TEXT,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS injuries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    team_name TEXT NOT NULL,
    player_name TEXT NOT NULL,
    player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    position TEXT,
    updated_at TEXT,
    injury TEXT,
    injury_status TEXT,
    cbs_player_id TEXT,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roster_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_label TEXT,
    source_url TEXT,
    rank INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    positions TEXT,
    nba_team TEXT,
    roster_status TEXT,
    recent_views TEXT,
    today_views TEXT,
    owned_pct TEXT,
    start_pct TEXT,
    adds TEXT,
    drops TEXT,
    trades TEXT,
    total TEXT,
    external_id TEXT,
    scraped_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL,
    link_id TEXT NOT NULL,
    link_label TEXT,
    UNIQUE(note_id, link_type, link_id)
);

CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_stats(player_id, record_type);
CREATE INDEX IF NOT EXISTS idx_depth_player ON player_depth_chart(player_id, position);
CREATE INDEX IF NOT EXISTS idx_fantasy_news_published ON fantasy_news(published_iso DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_fantasy_news_player ON fantasy_news(player_id);
CREATE INDEX IF NOT EXISTS idx_injuries_team ON injuries(team_name, player_name);
CREATE INDEX IF NOT EXISTS idx_roster_trends_source ON roster_trends(source, rank);
CREATE INDEX IF NOT EXISTS idx_note_links_note ON note_links(note_id);
CREATE INDEX IF NOT EXISTS idx_note_links_target ON note_links(link_type, link_id);
"""

WATCHLIST_POSITION_ORDER = ("PG", "SG", "G", "SF", "PF", "F", "C", "Util")


def content_hash(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p).strip() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(players)").fetchall()}
    if "fantasy_owner" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN fantasy_owner TEXT")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    return conn


def upsert_player(conn: sqlite3.Connection, player: dict[str, Any], *, scraped_at: str) -> int:
    conn.execute(
        """
        INSERT INTO players (
            yahoo_id, first_name, last_name, full_name, positions, nba_team,
            profile_url, yahoo_rank, owned_pct, fantasy_owner, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(yahoo_id) DO UPDATE SET
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            full_name=excluded.full_name,
            positions=excluded.positions,
            nba_team=excluded.nba_team,
            profile_url=excluded.profile_url,
            yahoo_rank=excluded.yahoo_rank,
            owned_pct=excluded.owned_pct,
            fantasy_owner=COALESCE(excluded.fantasy_owner, players.fantasy_owner),
            updated_at=excluded.updated_at
        """,
        (
            player["yahoo_id"],
            player["first_name"],
            player["last_name"],
            player["full_name"],
            player.get("positions"),
            player.get("nba_team"),
            player.get("profile_url"),
            player.get("yahoo_rank"),
            player.get("owned_pct"),
            player.get("fantasy_owner"),
            scraped_at,
        ),
    )
    row = conn.execute(
        "SELECT id FROM players WHERE yahoo_id = ?", (player["yahoo_id"],)
    ).fetchone()
    return int(row["id"])


def set_fantasy_owners(
    owners: dict[str, str],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Set fantasy_owner for yahoo_ids. owners: yahoo_id → label (You / team / FA)."""
    if not owners:
        return 0
    own_conn = conn is None
    if own_conn:
        conn = connect()
    updated = 0
    try:
        for yahoo_id, label in owners.items():
            pid = str(yahoo_id or "").strip()
            owner = str(label or "").strip()
            if not pid or not owner:
                continue
            cur = conn.execute(
                "UPDATE players SET fantasy_owner = ? WHERE yahoo_id = ?",
                (owner, pid),
            )
            updated += int(cur.rowcount or 0)
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return updated


def set_yahoo_current_ranks(
    ranks: dict[str, int | str | None],
    *,
    owned: dict[str, str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Set yahoo_rank from Yahoo Rankings → Current (not Pre-Season).

    ranks values: positive int, or None to clear a stale Pre-Season value when
    Yahoo Current is blank. Optional owned: yahoo_id → %Ros (e.g. \"100%\").
    """
    if not ranks and not owned:
        return 0
    own_conn = conn is None
    if own_conn:
        conn = connect()
    updated = 0
    owned = owned or {}
    try:
        ids = set(ranks) | set(owned)
        for yahoo_id in ids:
            pid = str(yahoo_id or "").strip()
            if not pid:
                continue
            fields: list[str] = []
            vals: list[Any] = []
            if pid in ranks:
                raw_rank = ranks[pid]
                if raw_rank is None or str(raw_rank).strip() == "":
                    fields.append("yahoo_rank = ?")
                    vals.append(None)
                else:
                    try:
                        yrank = int(str(raw_rank).strip())
                    except (TypeError, ValueError):
                        yrank = None
                    if yrank is not None and yrank > 0:
                        fields.append("yahoo_rank = ?")
                        vals.append(yrank)
            if pid in owned:
                pct = str(owned[pid] or "").strip()
                if pct:
                    fields.append("owned_pct = ?")
                    vals.append(pct)
            if not fields:
                continue
            vals.append(pid)
            cur = conn.execute(
                f"UPDATE players SET {', '.join(fields)} WHERE yahoo_id = ?",
                vals,
            )
            updated += int(cur.rowcount or 0)
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return updated


def clear_stale_you_owners(
    keep_yahoo_ids: set[str] | list[str],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Players marked You but no longer on my roster → FA."""
    keep = {str(x).strip() for x in (keep_yahoo_ids or []) if str(x).strip()}
    own_conn = conn is None
    if own_conn:
        conn = connect()
    try:
        rows = conn.execute(
            "SELECT yahoo_id FROM players WHERE fantasy_owner = 'You'"
        ).fetchall()
        cleared = 0
        for r in rows:
            yid = str(r["yahoo_id"] or "").strip()
            if not yid or yid in keep:
                continue
            conn.execute(
                "UPDATE players SET fantasy_owner = ? WHERE yahoo_id = ?",
                ("FA", yid),
            )
            cleared += 1
        if own_conn:
            conn.commit()
        return cleared
    finally:
        if own_conn:
            conn.close()


def insert_stat_if_new(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    try:
        cols = [
            "player_id",
            "record_type",
            "published_at",
            "source",
            "title",
            "body",
            "game_date",
            "opponent",
            "is_home",
            "status",
            "minutes",
            "fgm_a",
            "fg_pct",
            "ftm_a",
            "ft_pct",
            "threes",
            "pts",
            "reb",
            "ast",
            "stl",
            "blk",
            "tov",
            "season",
            "games_played",
            "season_rank",
            "content_hash",
            "scraped_at",
        ]
        conn.execute(
            f"INSERT INTO player_stats ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.get(c) for c in cols),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def insert_depth_if_new(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    try:
        cols = [
            "player_id",
            "nba_team",
            "position",
            "depth_rank",
            "depth_player_name",
            "depth_yahoo_id",
            "yahoo_rank",
            "content_hash",
            "scraped_at",
        ]
        conn.execute(
            f"INSERT INTO player_depth_chart ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.get(c) for c in cols),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def parse_positions(positions: str | None) -> list[str]:
    if not positions:
        return []
    out: list[str] = []
    for part in str(positions).replace("/", ",").split(","):
        pos = part.strip()
        if pos and pos not in out:
            out.append(pos)
    return out


_NAME_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b", re.I)


def normalize_player_name(name: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(name or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = raw.lower().replace(".", " ")
    raw = _NAME_SUFFIX_RE.sub(" ", raw)
    raw = re.sub(r"[^a-z0-9\s'-]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def find_player_id_by_name(
    name: str | None, conn: sqlite3.Connection | None = None
) -> int | None:
    """Best-effort match of a display name to a local players.id."""
    needle = normalize_player_name(name)
    if not needle:
        return None
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, full_name, first_name, last_name FROM players"
        ).fetchall()
        exact: list[int] = []
        loose: list[int] = []
        last_only: list[int] = []
        parts = needle.split()
        last = parts[-1] if parts else ""
        first_token = parts[0] if parts else ""
        for r in rows:
            full = normalize_player_name(r["full_name"])
            first = normalize_player_name(r["first_name"])
            lname = normalize_player_name(r["last_name"])
            composed = normalize_player_name(f"{r['first_name']} {r['last_name']}")
            pid = int(r["id"])
            if needle in {full, composed}:
                exact.append(pid)
            elif full and (needle == full or composed == needle):
                exact.append(pid)
            elif full and (needle in full or full in needle or composed in needle or needle in composed):
                loose.append(pid)
            elif last and lname == last:
                if len(parts) == 1:
                    last_only.append(pid)
                elif first_token and first.startswith(first_token[0]):
                    last_only.append(pid)
        for bucket in (exact, loose, last_only):
            uniq = list(dict.fromkeys(bucket))
            if len(uniq) == 1:
                return uniq[0]
        return None
    finally:
        if own:
            conn.close()


def attach_fantasy_news_to_player_stats(
    conn: sqlite3.Connection, row: dict[str, Any], *, scraped_at: str
) -> bool:
    """Copy one fantasy_news row onto player_stats news for the matched player."""
    player_id = row.get("player_id")
    if not player_id:
        return False
    title = row.get("title") or ""
    body = row.get("body") or ""
    published = row.get("published_at") or row.get("published_iso") or ""
    source = row.get("source") or "NBA.com Fantasy News"
    ph = content_hash(player_id, "news", published, source, title, body)
    return insert_stat_if_new(
        conn,
        {
            "player_id": int(player_id),
            "record_type": "news",
            "published_at": published or None,
            "source": source,
            "title": title or None,
            "body": body or None,
            "content_hash": ph,
            "scraped_at": scraped_at,
        },
    )


_TITLE_PLAYER_RE = re.compile(
    r"^(?:[A-Za-z0-9 .&'-]+?)'\s*(.+?):\s*",
    re.IGNORECASE,
)


def _news_name_candidates(player_name: str | None, title: str | None) -> list[str]:
    """Possible display names for matching a news row to a DB player."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in (player_name, title):
        if not raw:
            continue
        text = str(raw).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    title_s = str(title or "").strip()
    if title_s:
        m = _TITLE_PLAYER_RE.match(title_s)
        if m:
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        # Fallback: "Player Name: headline" without Team's prefix
        if ":" in title_s:
            head = title_s.split(":", 1)[0].strip()
            if head and len(head) < 60 and head not in seen:
                seen.add(head)
                out.append(head)
    return out


def relink_fantasy_news(conn: sqlite3.Connection, *, scraped_at: str | None = None) -> dict[str, int]:
    """Re-match fantasy_news to players and ensure player_stats news rows exist."""
    from datetime import datetime, timezone

    ts = scraped_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    linked = 0
    synced = 0
    still_unmatched = 0
    rows = conn.execute(
        """
        SELECT id, player_name, player_id, published_at, published_iso,
               source, title, body
        FROM fantasy_news
        """
    ).fetchall()
    for r in rows:
        row = dict(r)
        pid = row.get("player_id")
        if not pid:
            for cand in _news_name_candidates(row.get("player_name"), row.get("title")):
                pid = find_player_id_by_name(cand, conn=conn)
                if pid:
                    break
            if pid:
                conn.execute(
                    "UPDATE fantasy_news SET player_id = ? WHERE id = ?",
                    (pid, row["id"]),
                )
                row["player_id"] = pid
                linked += 1
            else:
                still_unmatched += 1
        if row.get("player_id") and attach_fantasy_news_to_player_stats(
            conn, row, scraped_at=ts
        ):
            synced += 1
    return {
        "relinked": linked,
        "player_news_synced": synced,
        "still_unmatched": still_unmatched,
    }


def sync_fantasy_news_links() -> dict[str, Any]:
    """Public helper: re-link unmatched fantasy news to current DB players."""
    conn = connect()
    try:
        result = relink_fantasy_news(conn)
        conn.commit()
        counts = fantasy_news_counts(conn)
        return {"ok": True, **result, **counts}
    finally:
        conn.close()


def insert_fantasy_news_if_new(
    conn: sqlite3.Connection, row: dict[str, Any]
) -> bool:
    try:
        cols = [
            "content_hash",
            "player_name",
            "player_id",
            "nba_player_id",
            "published_at",
            "published_iso",
            "source",
            "title",
            "body",
            "team_name",
            "scraped_at",
        ]
        conn.execute(
            f"INSERT INTO fantasy_news ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(row.get(c) for c in cols),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def list_fantasy_news(
    *,
    limit: int = 100,
    matched_only: bool = False,
    unmatched_only: bool = False,
    match: str | None = None,
    sources: list[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect()
    try:
        limit = max(1, min(int(limit), 500))
        match_mode = (match or "").strip().lower()
        if matched_only:
            match_mode = "matched"
        if unmatched_only:
            match_mode = "unmatched"

        clauses: list[str] = []
        params: list[Any] = []
        if match_mode == "matched":
            clauses.append("f.player_id IS NOT NULL")
        elif match_mode == "unmatched":
            clauses.append("f.player_id IS NULL")

        clean_sources = [str(s).strip() for s in (sources or []) if str(s).strip()]
        if clean_sources:
            placeholders = ", ".join("?" for _ in clean_sources)
            clauses.append(f"f.source IN ({placeholders})")
            params.extend(clean_sources)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"""
            SELECT
                f.id,
                f.player_name,
                f.player_id,
                f.nba_player_id,
                f.published_at,
                f.published_iso,
                f.source,
                f.title,
                f.body,
                f.team_name,
                f.scraped_at,
                p.full_name AS matched_name,
                p.positions,
                p.nba_team,
                p.yahoo_rank
            FROM fantasy_news f
            LEFT JOIN players p ON p.id = f.player_id
            {where}
            ORDER BY
                CASE WHEN f.published_iso IS NULL OR f.published_iso = '' THEN 1 ELSE 0 END,
                f.published_iso DESC,
                f.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def fantasy_news_counts(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM fantasy_news").fetchone()["c"]
        matched = conn.execute(
            "SELECT COUNT(*) AS c FROM fantasy_news WHERE player_id IS NOT NULL"
        ).fetchone()["c"]
        by_source_rows = conn.execute(
            """
            SELECT COALESCE(source, '') AS source, COUNT(*) AS c
            FROM fantasy_news
            GROUP BY COALESCE(source, '')
            ORDER BY c DESC, source
            """
        ).fetchall()
        return {
            "total": int(total),
            "matched": int(matched),
            "unmatched": int(total) - int(matched),
            "by_source": {str(r["source"] or ""): int(r["c"]) for r in by_source_rows},
            "sources": [str(r["source"] or "") for r in by_source_rows if r["source"]],
        }
    finally:
        if own:
            conn.close()


def replace_injuries(
    conn: sqlite3.Connection, rows: list[dict[str, Any]], *, scraped_at: str
) -> dict[str, int]:
    """Replace the full injuries snapshot and link known players."""
    conn.execute("DELETE FROM injuries")
    inserted = 0
    matched = 0
    for row in rows:
        name = (row.get("player_name") or "").strip()
        team = (row.get("team_name") or "").strip()
        if not name or not team:
            continue
        player_id = find_player_id_by_name(name, conn=conn)
        if player_id:
            matched += 1
        h = content_hash(
            "injury",
            team,
            name,
            row.get("position"),
            row.get("updated_at"),
            row.get("injury"),
            row.get("injury_status"),
            row.get("cbs_player_id"),
        )
        try:
            conn.execute(
                """
                INSERT INTO injuries (
                    content_hash, team_name, player_name, player_id, position,
                    updated_at, injury, injury_status, cbs_player_id, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    h,
                    team,
                    name,
                    player_id,
                    (row.get("position") or None),
                    (row.get("updated_at") or None),
                    (row.get("injury") or None),
                    (row.get("injury_status") or None),
                    (row.get("cbs_player_id") or None),
                    scraped_at,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            continue
    return {"inserted": inserted, "matched": matched}


def list_injuries_by_team(
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    from nba_teams import enrich_team_group

    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                i.id,
                i.team_name,
                i.player_name,
                i.player_id,
                i.position,
                i.updated_at,
                i.injury,
                i.injury_status,
                i.cbs_player_id,
                i.scraped_at,
                p.full_name AS matched_name,
                p.yahoo_rank
            FROM injuries i
            LEFT JOIN players p ON p.id = i.player_id
            ORDER BY i.team_name COLLATE NOCASE, i.player_name COLLATE NOCASE
            """
        ).fetchall()
        groups_map: dict[str, list[dict[str, Any]]] = {}
        scraped_at = None
        for r in rows:
            item = dict(r)
            team = item.get("team_name") or "Unknown"
            groups_map.setdefault(team, []).append(item)
            if not scraped_at and item.get("scraped_at"):
                scraped_at = item["scraped_at"]
        groups = []
        for team, players in groups_map.items():
            meta = enrich_team_group(team)
            groups.append(
                {
                    "team": team,
                    "abbr": meta.get("abbr"),
                    "logo": meta.get("logo"),
                    "display_name": meta.get("display_name") or team,
                    "count": len(players),
                    "players": players,
                }
            )
        return {
            "groups": groups,
            "team_count": len(groups),
            "player_count": len(rows),
            "scraped_at": scraped_at,
        }
    finally:
        if own:
            conn.close()


def replace_roster_trends(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_label: str,
    source_url: str,
    rows: list[dict[str, Any]],
    scraped_at: str,
) -> dict[str, int]:
    """Replace one trends source snapshot and link known players."""
    conn.execute("DELETE FROM roster_trends WHERE source = ?", (source,))
    inserted = 0
    matched = 0
    for row in rows:
        name = (row.get("player_name") or "").strip()
        if not name:
            continue
        player_id = find_player_id_by_name(name, conn=conn)
        if player_id:
            matched += 1
        conn.execute(
            """
            INSERT INTO roster_trends (
                source, source_label, source_url, rank, player_name, player_id,
                positions, nba_team, roster_status, recent_views, today_views,
                owned_pct, start_pct, adds, drops, trades, total, external_id, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                source_label,
                source_url,
                int(row.get("rank") or 0),
                name,
                player_id,
                (row.get("positions") or None),
                (row.get("nba_team") or None),
                (row.get("roster_status") or None),
                (row.get("recent_views") or None),
                (row.get("today_views") or None),
                (row.get("owned_pct") or None),
                (row.get("start_pct") or None),
                (row.get("adds") or None),
                (row.get("drops") or None),
                (row.get("trades") or None),
                (row.get("total") or None),
                (row.get("external_id") or None),
                scraped_at,
            ),
        )
        inserted += 1
    return {"inserted": inserted, "matched": matched}


def list_roster_trends(
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                t.*,
                p.full_name AS matched_name,
                p.yahoo_rank
            FROM roster_trends t
            LEFT JOIN players p ON p.id = t.player_id
            ORDER BY t.source, t.rank ASC, t.id ASC
            """
        ).fetchall()
        by_source: dict[str, dict[str, Any]] = {}
        for r in rows:
            item = dict(r)
            src = item.get("source") or "unknown"
            bucket = by_source.setdefault(
                src,
                {
                    "source": src,
                    "source_label": item.get("source_label") or src,
                    "source_url": item.get("source_url"),
                    "scraped_at": item.get("scraped_at"),
                    "players": [],
                },
            )
            bucket["players"].append(item)
        # Stable order: CBS first, then Yahoo
        order = ["cbs_viewed", "yahoo_transactions"]
        sections = []
        seen = set()
        for key in order:
            if key in by_source:
                sections.append(by_source[key])
                seen.add(key)
        for key, val in by_source.items():
            if key not in seen:
                sections.append(val)
        return {
            "sections": sections,
            "player_count": len(rows),
            "section_count": len(sections),
        }
    finally:
        if own:
            conn.close()


def list_players_by_team(
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """DB players grouped by NBA team, with that team's news attached per group."""
    from nba_teams import enrich_team_group, team_keys_for

    own = conn is None
    if own:
        conn = connect()
    try:
        players = list_players(conn=conn)
        buckets: dict[str, list[dict[str, Any]]] = {}
        for p in players:
            team = (p.get("nba_team") or "").strip() or "—"
            buckets.setdefault(team, []).append(p)

        player_team_by_id = {
            int(p["id"]): (p.get("nba_team") or "").strip()
            for p in players
            if p.get("id") is not None
        }

        all_news = list_news_for_db_players(limit=500, conn=conn)
        news_by_team: dict[str, list[dict[str, Any]]] = {t: [] for t in buckets}
        team_key_map = {team: team_keys_for(team) for team in buckets}

        for item in all_news:
            item_keys = set()
            item_keys |= team_keys_for(item.get("team_name"))
            item_keys |= team_keys_for(item.get("nba_team"))
            pid = item.get("player_id")
            if pid is not None:
                item_keys |= team_keys_for(player_team_by_id.get(int(pid)))

            placed = False
            if item_keys:
                for team, keys in team_key_map.items():
                    if keys & item_keys:
                        news_by_team[team].append(item)
                        placed = True
            if placed:
                continue
            if pid is not None:
                team = player_team_by_id.get(int(pid))
                if team and team in news_by_team:
                    news_by_team[team].append(item)

        groups: list[dict[str, Any]] = []
        for team in sorted(buckets.keys(), key=lambda t: (t == "—", t)):
            roster = buckets[team]
            roster.sort(
                key=lambda p: (
                    p.get("yahoo_rank") is None,
                    p.get("yahoo_rank") if p.get("yahoo_rank") is not None else 10**9,
                    p.get("full_name") or "",
                )
            )
            team_news = news_by_team.get(team) or []
            meta = enrich_team_group(team)
            groups.append(
                {
                    "team": team,
                    "abbr": meta.get("abbr"),
                    "logo": meta.get("logo"),
                    "display_name": meta.get("display_name") or team,
                    "players": roster,
                    "count": len(roster),
                    "news": team_news,
                    "news_count": len(team_news),
                }
            )

        return {
            "groups": groups,
            "players": players,
            "player_count": len(players),
            "team_count": len(groups),
        }
    finally:
        if own:
            conn.close()


def list_news_for_db_players(
    *,
    limit: int = 200,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """All news tied to players that exist in the local DB (Yahoo + NBA.com)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        limit = max(1, min(int(limit), 500))
        fantasy = [
            dict(r)
            for r in conn.execute(
                """
                SELECT
                    f.published_at,
                    f.published_iso,
                    f.source,
                    f.title,
                    f.body,
                    f.player_id,
                    f.player_name,
                    f.team_name,
                    p.full_name AS matched_name,
                    p.nba_team,
                    p.positions,
                    p.yahoo_rank
                FROM fantasy_news f
                JOIN players p ON p.id = f.player_id
                ORDER BY
                    CASE WHEN f.published_iso IS NULL OR f.published_iso = '' THEN 1 ELSE 0 END,
                    f.published_iso DESC,
                    f.id DESC
                """
            ).fetchall()
        ]
        yahoo = [
            dict(r)
            for r in conn.execute(
                """
                SELECT
                    s.published_at,
                    NULL AS published_iso,
                    s.source,
                    s.title,
                    s.body,
                    s.player_id,
                    p.full_name AS player_name,
                    p.nba_team AS team_name,
                    p.full_name AS matched_name,
                    p.nba_team,
                    p.positions,
                    p.yahoo_rank
                FROM player_stats s
                JOIN players p ON p.id = s.player_id
                WHERE s.record_type = 'news'
                ORDER BY s.id DESC
                """
            ).fetchall()
        ]

        by_title: dict[str, dict[str, Any]] = {}
        untitled: list[dict[str, Any]] = []
        for row in fantasy + yahoo:
            title_key = normalize_player_name(row.get("title") or "")
            if not title_key:
                untitled.append(row)
                continue
            prev = by_title.get(title_key)
            if prev is None or len(row.get("body") or "") > len(prev.get("body") or ""):
                by_title[title_key] = row
        merged = list(by_title.values()) + untitled

        with_iso = [n for n in merged if n.get("published_iso")]
        without_iso = [n for n in merged if not n.get("published_iso")]
        with_iso.sort(key=lambda n: str(n.get("published_iso") or ""), reverse=True)
        merged = with_iso + without_iso
        return merged[:limit]
    finally:
        if own:
            conn.close()


def list_players(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                p.*,
                CASE WHEN w.player_id IS NOT NULL THEN 1 ELSE 0 END AS is_favourite,
                s.games_played AS gp,
                s.fg_pct,
                s.ft_pct,
                s.threes,
                s.pts,
                s.reb,
                s.ast,
                s.stl,
                s.blk,
                s.tov
            FROM players p
            LEFT JOIN watchlist w ON w.player_id = p.id
            LEFT JOIN player_stats s ON s.id = (
                SELECT s2.id FROM player_stats s2
                WHERE s2.player_id = p.id AND s2.record_type = 'season_avg'
                ORDER BY s2.season DESC, s2.id DESC
                LIMIT 1
            )
            ORDER BY
                CASE WHEN p.yahoo_rank IS NULL THEN 1 ELSE 0 END,
                p.yahoo_rank ASC,
                p.full_name ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def get_yahoo_ids(conn: sqlite3.Connection | None = None) -> set[str]:
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute("SELECT yahoo_id FROM players").fetchall()
        return {str(r["yahoo_id"]) for r in rows}
    finally:
        if own:
            conn.close()


def get_player_by_id(
    player_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        return _row_to_dict(
            conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        )
    finally:
        if own:
            conn.close()


def get_player_by_yahoo_id(
    yahoo_id: str, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        return _row_to_dict(
            conn.execute(
                "SELECT * FROM players WHERE yahoo_id = ?", (str(yahoo_id),)
            ).fetchone()
        )
    finally:
        if own:
            conn.close()


def delete_player(player_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """Delete one player and related stats/depth rows. Returns deleted player or None."""
    own = conn is None
    if own:
        conn = connect()
    try:
        player = _row_to_dict(
            conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        )
        if not player:
            return None
        conn.execute("DELETE FROM player_stats WHERE player_id = ?", (player_id,))
        conn.execute("DELETE FROM player_depth_chart WHERE player_id = ?", (player_id,))
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        if own:
            conn.commit()
        return player
    finally:
        if own:
            conn.close()


def delete_players(player_ids: list[int]) -> dict[str, Any]:
    """Delete many players. Returns counts and deleted names."""
    ids = [int(x) for x in player_ids if x is not None]
    if not ids:
        return {"deleted_count": 0, "deleted": [], "missing_ids": []}

    conn = connect()
    try:
        deleted: list[dict[str, Any]] = []
        missing: list[int] = []
        for pid in ids:
            row = delete_player(pid, conn=conn)
            if row:
                deleted.append(
                    {
                        "id": row["id"],
                        "yahoo_id": row.get("yahoo_id"),
                        "full_name": row.get("full_name"),
                    }
                )
            else:
                missing.append(pid)
        conn.commit()
        return {
            "deleted_count": len(deleted),
            "deleted": deleted,
            "missing_ids": missing,
        }
    finally:
        conn.close()


def clear_players_db(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Wipe all player-related tables."""
    own = conn is None
    if own:
        conn = connect()
    try:
        players = conn.execute("SELECT COUNT(*) AS c FROM players").fetchone()["c"]
        stats = conn.execute("SELECT COUNT(*) AS c FROM player_stats").fetchone()["c"]
        depth = conn.execute("SELECT COUNT(*) AS c FROM player_depth_chart").fetchone()["c"]
        watch = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
        conn.execute("DELETE FROM watchlist")
        conn.execute("DELETE FROM player_stats")
        conn.execute("DELETE FROM player_depth_chart")
        conn.execute("DELETE FROM players")
        conn.commit()
        return {
            "players_deleted": int(players),
            "stats_deleted": int(stats),
            "depth_deleted": int(depth),
            "watchlist_deleted": int(watch),
        }
    finally:
        if own:
            conn.close()


def get_player_detail(
    player_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        player = _row_to_dict(
            conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        )
        if not player:
            return None
        fav = conn.execute(
            "SELECT 1 FROM watchlist WHERE player_id = ?", (player_id,)
        ).fetchone()
        player["is_favourite"] = 1 if fav else 0
        news_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT published_at, source, title, body, scraped_at, NULL AS published_iso
                FROM player_stats
                WHERE player_id = ? AND record_type = 'news'
                """,
                (player_id,),
            ).fetchall()
        ]
        fantasy_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT published_at, source, title, body, scraped_at, published_iso
                FROM fantasy_news
                WHERE player_id = ?
                """,
                (player_id,),
            ).fetchall()
        ]
        # Dedupe by title (Yahoo often re-saves with a longer body; NBA.com may
        # mirror the same Rotowire headline). Keep the longest body.
        by_title: dict[str, dict[str, Any]] = {}
        untitled: list[dict[str, Any]] = []
        for row in fantasy_rows + news_rows:
            title_key = normalize_player_name(row.get("title") or "")
            if not title_key:
                untitled.append(row)
                continue
            prev = by_title.get(title_key)
            if prev is None or len(row.get("body") or "") > len(prev.get("body") or ""):
                by_title[title_key] = row
        news = list(by_title.values()) + untitled

        with_iso = [n for n in news if n.get("published_iso")]
        without_iso = [n for n in news if not n.get("published_iso")]
        with_iso.sort(key=lambda n: str(n.get("published_iso") or ""), reverse=True)
        news = with_iso + without_iso
        game_logs = [
            dict(r)
            for r in conn.execute(
                """
                SELECT game_date, opponent, is_home, status, minutes,
                       fgm_a, fg_pct, ftm_a, ft_pct, threes,
                       pts, reb, ast, stl, blk, tov, scraped_at
                FROM player_stats
                WHERE player_id = ? AND record_type = 'game_log'
                ORDER BY id DESC
                """,
                (player_id,),
            ).fetchall()
        ]
        season = [
            dict(r)
            for r in conn.execute(
                """
                SELECT record_type, season, games_played, season_rank,
                       fgm_a, fg_pct, ftm_a, ft_pct, threes,
                       pts, reb, ast, stl, blk, tov, scraped_at
                FROM player_stats
                WHERE player_id = ? AND record_type IN ('season_avg', 'season_total')
                ORDER BY season DESC, record_type
                """,
                (player_id,),
            ).fetchall()
        ]
        depth = [
            dict(r)
            for r in conn.execute(
                """
                SELECT nba_team, position, depth_rank, depth_player_name,
                       depth_yahoo_id, yahoo_rank, scraped_at
                FROM player_depth_chart
                WHERE player_id = ?
                ORDER BY position, depth_rank
                """,
                (player_id,),
            ).fetchall()
        ]
        return {
            "player": player,
            "news": news,
            "game_logs": game_logs,
            "season": season,
            "depth": depth,
        }
    finally:
        if own:
            conn.close()


def is_favourite(player_id: int, conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    if own:
        conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE player_id = ?", (player_id,)
        ).fetchone()
        return row is not None
    finally:
        if own:
            conn.close()


def add_favourite(
    player_id: int, *, scraped_at: str | None = None, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    from datetime import datetime, timezone

    own = conn is None
    if own:
        conn = connect()
    try:
        player = get_player_by_id(player_id, conn=conn)
        if not player:
            return None
        ts = scraped_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            """
            INSERT INTO watchlist (player_id, created_at) VALUES (?, ?)
            ON CONFLICT(player_id) DO NOTHING
            """,
            (player_id, ts),
        )
        conn.commit()
        player["is_favourite"] = 1
        return player
    finally:
        if own:
            conn.close()


def remove_favourite(
    player_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        player = get_player_by_id(player_id, conn=conn)
        if not player:
            return None
        conn.execute("DELETE FROM watchlist WHERE player_id = ?", (player_id,))
        conn.commit()
        player["is_favourite"] = 0
        return player
    finally:
        if own:
            conn.close()


def clear_watchlist(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    own = conn is None
    if own:
        conn = connect()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
        conn.execute("DELETE FROM watchlist")
        conn.commit()
        return {"cleared": int(count)}
    finally:
        if own:
            conn.close()


def list_watchlist(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT
                p.*,
                1 AS is_favourite,
                w.created_at AS favourited_at,
                s.games_played AS gp,
                s.fg_pct,
                s.ft_pct,
                s.threes,
                s.pts,
                s.reb,
                s.ast,
                s.stl,
                s.blk,
                s.tov
            FROM watchlist w
            JOIN players p ON p.id = w.player_id
            LEFT JOIN player_stats s ON s.id = (
                SELECT s2.id FROM player_stats s2
                WHERE s2.player_id = p.id AND s2.record_type = 'season_avg'
                ORDER BY s2.season DESC, s2.id DESC
                LIMIT 1
            )
            ORDER BY
                CASE WHEN p.yahoo_rank IS NULL THEN 1 ELSE 0 END,
                p.yahoo_rank ASC,
                p.full_name ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def list_watchlist_by_position(
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    players = list_watchlist(conn=conn)
    buckets: dict[str, list[dict[str, Any]]] = {
        pos: [] for pos in WATCHLIST_POSITION_ORDER
    }
    other: dict[str, list[dict[str, Any]]] = {}
    for p in players:
        positions = parse_positions(p.get("positions"))
        if not positions:
            other.setdefault("—", []).append(p)
            continue
        for pos in positions:
            key = pos if pos in buckets else pos
            if key in buckets:
                buckets[key].append(p)
            else:
                other.setdefault(key, []).append(p)

    groups: list[dict[str, Any]] = []
    for pos in WATCHLIST_POSITION_ORDER:
        groups.append({"position": pos, "players": buckets[pos], "count": len(buckets[pos])})
    for pos in sorted(other.keys()):
        groups.append({"position": pos, "players": other[pos], "count": len(other[pos])})

    return {
        "players": players,
        "groups": groups,
        "count": len(players),
    }


_NOTE_LINK_TYPES = frozenset({"player", "nba_team", "fantasy_team"})


def _note_now() -> str:
    from store import utc_now_iso

    return utc_now_iso()


def _note_links(conn: sqlite3.Connection, note_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, note_id, link_type, link_id, link_label
        FROM note_links
        WHERE note_id = ?
        ORDER BY link_type ASC, link_label ASC, id ASC
        """,
        (note_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _note_dict(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    note = dict(row)
    note["links"] = _note_links(conn, int(note["id"]))
    return note


def list_notes(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [_note_dict(conn, r) for r in rows]
    finally:
        if own:
            conn.close()


def get_note(note_id: int, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        if not row:
            return None
        return _note_dict(conn, row)
    finally:
        if own:
            conn.close()


def _replace_note_links(
    conn: sqlite3.Connection, note_id: int, links: list[dict[str, Any]] | None
) -> None:
    conn.execute("DELETE FROM note_links WHERE note_id = ?", (note_id,))
    if not links:
        return
    seen: set[tuple[str, str]] = set()
    for link in links:
        link_type = str((link or {}).get("link_type") or "").strip()
        link_id = str((link or {}).get("link_id") or "").strip()
        if link_type not in _NOTE_LINK_TYPES or not link_id:
            continue
        key = (link_type, link_id)
        if key in seen:
            continue
        seen.add(key)
        label = str((link or {}).get("link_label") or link_id).strip() or link_id
        conn.execute(
            """
            INSERT INTO note_links (note_id, link_type, link_id, link_label)
            VALUES (?, ?, ?, ?)
            """,
            (note_id, link_type, link_id, label),
        )


def create_note(
    *,
    title: str | None = None,
    body: str = "",
    links: list[dict[str, Any]] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = conn is None
    if own:
        conn = connect()
    try:
        now = _note_now()
        cur = conn.execute(
            """
            INSERT INTO notes (title, body, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            ((title or "").strip() or None, body or "", now, now),
        )
        note_id = int(cur.lastrowid)
        _replace_note_links(conn, note_id, links)
        if own:
            conn.commit()
        note = get_note(note_id, conn=conn)
        assert note is not None
        return note
    finally:
        if own:
            conn.close()


def update_note(
    note_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    links: list[dict[str, Any]] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = connect()
    try:
        existing = conn.execute(
            "SELECT * FROM notes WHERE id = ?", (int(note_id),)
        ).fetchone()
        if not existing:
            return None
        new_title = existing["title"] if title is None else ((title or "").strip() or None)
        new_body = existing["body"] if body is None else (body or "")
        conn.execute(
            """
            UPDATE notes SET title = ?, body = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_title, new_body, _note_now(), int(note_id)),
        )
        if links is not None:
            _replace_note_links(conn, int(note_id), links)
        if own:
            conn.commit()
        return get_note(int(note_id), conn=conn)
    finally:
        if own:
            conn.close()


def delete_note(note_id: int, conn: sqlite3.Connection | None = None) -> bool:
    own = conn is None
    if own:
        conn = connect()
    try:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (int(note_id),))
        if own:
            conn.commit()
        return cur.rowcount > 0
    finally:
        if own:
            conn.close()
