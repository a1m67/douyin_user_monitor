from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_PREFIX = "app-"
MAINTENANCE_BUSY_TIMEOUT_MS = 30_000
RESTORE_LOCK_TIMEOUT_SECONDS = 0.25


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
    verification = verify_backup(target, verify_hash=False)
    manifest = {
        "filename": target.name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256_file(target),
        "schema_version": verification["schema_version"],
    }
    _write_json_atomic(target.with_suffix(".json"), manifest)
    backups = sorted(target_dir.glob(f"{BACKUP_PREFIX}[0-9]*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    for expired in backups[max(1, retention_count):]:
        expired.unlink()
        expired.with_suffix(".json").unlink(missing_ok=True)
    return target


def latest_backup(database_path: Path) -> Path:
    backup_dir = Path(database_path).resolve().parent / "backups"
    backups = sorted(
        backup_dir.glob(f"{BACKUP_PREFIX}[0-9]*.db"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        raise FileNotFoundError("没有可用备份")
    return backups[0]


def verify_backup(backup_path: Path, *, verify_hash: bool = True) -> dict[str, Any]:
    path = Path(backup_path).expanduser().resolve()
    manifest_path = path.with_suffix(".json")
    checks: dict[str, Any] = {
        "exists": path.is_file(),
        "manifest": manifest_path.is_file(),
        "hash": "not_available",
        "integrity": None,
        "foreign_key_errors": [],
    }
    schema_version = 0
    error: str | None = None
    if not path.is_file():
        return {"ok": False, "file": path.name, "schema_version": 0, "checks": checks, "error": "备份文件不存在"}
    try:
        if verify_hash and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = str(manifest.get("sha256") or "").lower()
            actual = _sha256_file(path)
            checks["hash"] = "ok" if expected and expected == actual else "mismatch"
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            checks["integrity"] = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            checks["foreign_key_errors"] = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            row = connection.execute(
                "SELECT value FROM app_meta WHERE key='schema_version'"
            ).fetchone()
            schema_version = int(row[0]) if row else 0
        finally:
            connection.close()
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        error = str(exc)
    hash_ok = checks["hash"] in {"ok", "not_available"} or not verify_hash
    ok = error is None and hash_ok and checks["integrity"] == "ok" and not checks["foreign_key_errors"]
    return {
        "ok": ok,
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "schema_version": schema_version,
        "checks": checks,
        "error": error,
    }


def restore_database(
    database_path: Path,
    *,
    source_path: Path,
    dry_run: bool = False,
    confirmed: bool = False,
    retention_count: int = 14,
) -> dict[str, Any]:
    target = Path(database_path).expanduser().resolve()
    source = Path(source_path).expanduser().resolve()
    verification = verify_backup(source)
    if not verification["ok"]:
        raise ValueError(f"备份验证失败: {verification['error'] or verification['checks']}")
    from douyin_user_monitor.repositories.sqlite import SCHEMA_VERSION

    if int(verification["schema_version"]) > SCHEMA_VERSION:
        raise ValueError(
            f"备份 schema v{verification['schema_version']} 高于当前程序 v{SCHEMA_VERSION}"
        )
    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "source": source.name,
        "target": target.name,
        "schema_version": int(verification["schema_version"]),
        "current_schema_version": SCHEMA_VERSION,
        "verification": verification,
        "pre_restore_backup": None,
        "rolled_back": False,
    }
    if dry_run:
        return result
    if not confirmed:
        raise ValueError("真实恢复必须显式传入 --yes")
    if not target.is_file():
        raise FileNotFoundError("目标数据库不存在")

    _assert_database_inactive(target)
    pre_restore = backup_database(target, retention_count=retention_count)
    result["pre_restore_backup"] = pre_restore.name
    temp_path = _copy_to_fsynced_temp(source, target.parent)
    try:
        _assert_database_inactive(target)
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
        for suffix in ("-wal", "-shm"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        restored = verify_backup(target, verify_hash=False)
        if not restored["ok"]:
            raise RuntimeError(f"恢复后数据库验证失败: {restored['error'] or restored['checks']}")
        result["verification"] = restored
        return result
    except Exception:
        rollback_temp = _copy_to_fsynced_temp(pre_restore, target.parent)
        os.replace(rollback_temp, target)
        _fsync_directory(target.parent)
        for suffix in ("-wal", "-shm"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        result["rolled_back"] = True
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def _assert_database_inactive(database_path: Path) -> None:
    connection = sqlite3.connect(database_path, timeout=RESTORE_LOCK_TIMEOUT_SECONDS)
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(RESTORE_LOCK_TIMEOUT_SECONDS * 1000)}")
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("数据库正在使用中，请先停止 Docker/Application 后再恢复") from exc
    finally:
        connection.close()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _copy_to_fsynced_temp(source: Path, target_dir: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".restore-", suffix=".db", dir=target_dir)
    temp_path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as destination, source.open("rb") as source_file:
            shutil.copyfileobj(source_file, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def passive_wal_checkpoint(database_path: Path) -> dict[str, int]:
    """Checkpoint reusable WAL frames without truncating or blocking writers."""
    connection = sqlite3.connect(database_path, timeout=MAINTENANCE_BUSY_TIMEOUT_MS / 1000)
    try:
        row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return {"busy": int(row[0]), "log_frames": int(row[1]), "checkpointed_frames": int(row[2])}
    finally:
        connection.close()


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
        ai_usage_duplicates = int(connection.execute("""SELECT COUNT(*) FROM (
            SELECT usage_date,provider,COUNT(*) n FROM ai_usage_daily
            GROUP BY usage_date,provider HAVING n>1)""").fetchone()[0])
    finally:
        connection.close()
    checks = {"integrity": integrity, "foreign_key_errors": foreign_keys, "first_source_mismatch": first_source_mismatch, "duplicate_logical_episodes": duplicate_episodes, "stale_show_summary": stale_shows, "missing_show_seasons": missing_show_seasons, "watch_progress_orphans": watch_progress_orphans, "ai_usage_duplicates": ai_usage_duplicates}
    return DoctorReport(ok=integrity == "ok" and not foreign_keys and first_source_mismatch == 0 and duplicate_episodes == 0 and stale_shows == 0 and missing_show_seasons == 0 and watch_progress_orphans == 0 and ai_usage_duplicates == 0, checks=checks, repaired=repair)
