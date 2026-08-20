from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_PREFIX = "app-"


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
    finally:
        connection.close()
    checks = {"integrity": integrity, "foreign_key_errors": foreign_keys, "first_source_mismatch": first_source_mismatch, "duplicate_logical_episodes": duplicate_episodes, "stale_show_summary": stale_shows}
    return DoctorReport(ok=integrity == "ok" and not foreign_keys and first_source_mismatch == 0 and duplicate_episodes == 0 and stale_shows == 0, checks=checks, repaired=repair)
