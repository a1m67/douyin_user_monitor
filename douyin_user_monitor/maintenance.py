from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_PREFIX = "app-"
MAINTENANCE_BUSY_TIMEOUT_MS = 30_000


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def backup_database(database_path: Path, *, backup_dir: Path | None = None, retention_count: int = 14) -> Path:
    source_path = Path(database_path).resolve()
    target_dir = (backup_dir or source_path.parent / "backups").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    target = target_dir / f"{BACKUP_PREFIX}{stamp}.db"
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    backups = sorted(target_dir.glob(f"{BACKUP_PREFIX}[0-9]*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    for expired in backups[max(1, retention_count):]:
        expired.unlink()
    return target


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    checks: dict[str, Any]
    repaired: bool = False


def database_stats(database_path: Path, *, checkpoint: bool = False) -> dict[str, Any]:
    """Return bounded operational SQLite statistics without exposing row data."""
    path = Path(database_path).resolve()
    connection = sqlite3.connect(path, timeout=MAINTENANCE_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {MAINTENANCE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    checkpoint_result: dict[str, int] | None = None
    try:
        if checkpoint:
            row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            checkpoint_result = {
                "busy": int(row[0]),
                "log_frames": int(row[1]),
                "checkpointed_frames": int(row[2]),
            }
        table_names = [
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        ]
        table_rows = {
            name: int(
                connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}").fetchone()[0]
            )
            for name in table_names
        }
        indexes = [
            {
                "name": str(row["name"]),
                "table": str(row["tbl_name"]),
                "sql": str(row["sql"] or ""),
            }
            for row in connection.execute(
                """
                SELECT name, tbl_name, sql FROM sqlite_master
                WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
                ORDER BY tbl_name, name
                """
            )
        ]
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    finally:
        connection.close()
    wal_path = Path(f"{path}-wal")
    return {
        "database_path": str(path),
        "database_size_bytes": path.stat().st_size if path.is_file() else 0,
        "wal_size_bytes": wal_path.stat().st_size if wal_path.is_file() else 0,
        "journal_mode": journal_mode,
        "page_size_bytes": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "table_rows": table_rows,
        "indexes": indexes,
        "checkpoint": checkpoint_result,
    }


def doctor_database(database_path: Path, *, repair: bool = False) -> DoctorReport:
    path = Path(database_path).resolve()
    if repair:
        from douyin_user_monitor.repositories.sqlite import ShortDramaRepository

        ShortDramaRepository(path).repair_episode_and_show_consistency()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        first_source_mismatch = int(connection.execute("""SELECT COUNT(*) FROM episodes e
            WHERE NOT EXISTS (SELECT 1 FROM episode_sources s WHERE s.episode_id=e.id AND s.video_id=e.first_video_id AND s.account_id=e.first_account_id)""").fetchone()[0])
        duplicate_episodes = int(connection.execute("""SELECT COUNT(*) FROM (
            SELECT show_id,season_number,episode_number,COUNT(*) n FROM episodes
            GROUP BY show_id,season_number,episode_number HAVING n>1)""").fetchone()[0])
        stale_shows = int(connection.execute("""SELECT COUNT(*) FROM shows s WHERE
            COALESCE(s.latest_season,-1) != COALESCE((SELECT season_number FROM episodes e WHERE e.show_id=s.id ORDER BY season_number DESC,episode_number DESC LIMIT 1),-1)
            OR COALESCE(s.latest_episode,-1) != COALESCE((SELECT episode_number FROM episodes e WHERE e.show_id=s.id ORDER BY season_number DESC,episode_number DESC LIMIT 1),-1)""").fetchone()[0])
        missing_show_seasons = int(connection.execute("""SELECT COUNT(*) FROM (
            SELECT DISTINCT e.show_id,e.season_number FROM episodes e
            LEFT JOIN show_seasons ss ON ss.show_id=e.show_id AND ss.season_number=e.season_number
            WHERE ss.id IS NULL)""").fetchone()[0])
        watch_progress_orphans = int(connection.execute("""SELECT COUNT(*) FROM watch_progress wp
            LEFT JOIN shows s ON s.id=wp.show_id WHERE s.id IS NULL""").fetchone()[0])
    finally:
        connection.close()
    checks = {"integrity": integrity, "foreign_key_errors": foreign_keys, "first_source_mismatch": first_source_mismatch, "duplicate_logical_episodes": duplicate_episodes, "stale_show_summary": stale_shows, "missing_show_seasons": missing_show_seasons, "watch_progress_orphans": watch_progress_orphans}
    return DoctorReport(ok=integrity == "ok" and not foreign_keys and first_source_mismatch == 0 and duplicate_episodes == 0 and stale_shows == 0 and missing_show_seasons == 0 and watch_progress_orphans == 0, checks=checks, repaired=repair)
