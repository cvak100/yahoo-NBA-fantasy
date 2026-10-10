"""SQLite player database — upsert with content-hash dedupe."""

from __future__ import annotations

import hashlib
import sqlite3
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

CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_stats(player_id, record_type);
CREATE INDEX IF NOT EXISTS idx_depth_player ON player_depth_chart(player_id, position);
"""

WATCHLIST_POSITION_ORDER = ("PG", "SG", "G", "SF", "PF", "F", "C", "Util")


def content_hash(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p).strip() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(SCHEMA)
    return conn


def upsert_player(conn: sqlite3.Connection, player: dict[str, Any], *, scraped_at: str) -> int:
    conn.execute(
        """
        INSERT INTO players (
            yahoo_id, first_name, last_name, full_name, positions, nba_team,
            profile_url, yahoo_rank, owned_pct, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(yahoo_id) DO UPDATE SET
            first_name=excluded.first_name,
            last_name=excluded.last_name,
            full_name=excluded.full_name,
            positions=excluded.positions,
            nba_team=excluded.nba_team,
            profile_url=excluded.profile_url,
            yahoo_rank=excluded.yahoo_rank,
            owned_pct=excluded.owned_pct,
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
            scraped_at,
        ),
    )
    row = conn.execute(
        "SELECT id FROM players WHERE yahoo_id = ?", (player["yahoo_id"],)
    ).fetchone()
    return int(row["id"])


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
        news = [
            dict(r)
            for r in conn.execute(
                """
                SELECT published_at, source, title, body, scraped_at
                FROM player_stats
                WHERE player_id = ? AND record_type = 'news'
                ORDER BY id DESC
                """,
                (player_id,),
            ).fetchall()
        ]
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
