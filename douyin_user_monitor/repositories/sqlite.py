"""SQLite persistence and legacy JSON migration for short-drama data."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from douyin_user_monitor.parsers.regex import normalize_title
from douyin_user_monitor.maintenance import backup_database


SCHEMA_VERSION = 14
SQLITE_BUSY_TIMEOUT_MS = 30_000
SHOW_STATUSES = frozenset({"updating", "completed", "paused"})
VIDEO_CLASSIFICATIONS = frozenset({"matched", "ignored", "review"})
VIDEO_CONTENT_TYPES = frozenset({"episode", "trailer", "show_content", "unknown", "non_drama"})
HISTORY_SYNC_STATUSES = frozenset({"idle", "pending", "running", "paused", "completed", "failed"})
_PLACEHOLDER_NICKNAMES = frozenset({"", "nan", "none", "null", "undefined", "n/a"})
_UNSET = object()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class EpisodeWriteResult:
    episode: dict[str, Any]
    source: dict[str, Any]
    is_new_episode: bool
    is_new_source: bool


class ShortDramaRepository:
    """Small transactional repository backed by a single SQLite database.

    A connection is opened per operation, which keeps the object usable by
    FastAPI request handlers and the background scheduler without sharing a
    thread-affine SQLite connection. The lock protects schema changes and
    short write transactions in the current process.
    """

    def __init__(self, database_path: Path | str, *, legacy_state_path: Path | str | None = None):
        self.database_path = Path(database_path).expanduser().resolve()
        self.legacy_state_path = (
            Path(legacy_state_path).expanduser().resolve() if legacy_state_path else None
        )
        self._lock = threading.RLock()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        journal = self._connect()
        try:
            journal.execute("PRAGMA journal_mode = WAL").fetchone()
        finally:
            journal.close()
        if self.database_path.is_file() and self.database_path.stat().st_size:
            probe = self._connect()
            try:
                row = probe.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
                existing_version = _schema_version(str(row[0]) if row else None)
            except sqlite3.Error:
                existing_version = 0
            finally:
                probe.close()
            if 0 < existing_version < SCHEMA_VERSION:
                backup_database(self.database_path)
        with self._transaction() as connection:
            self._create_schema(connection)
            previous_schema_version = _schema_version(self._get_meta(connection, "schema_version"))
            self._migrate_schema(connection, previous_schema_version)
            self._repair_placeholder_account_nicknames(connection)
            self._set_meta(connection, "schema_version", str(SCHEMA_VERSION))
            if self._get_meta(connection, "legacy_state_imported") is None:
                self._import_legacy_state(connection)
                self._repair_placeholder_account_nicknames(connection)
                self._set_meta(connection, "legacy_state_imported", utc_now())

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL,
                sec_uid TEXT NOT NULL UNIQUE,
                homepage_url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                check_interval_minutes INTEGER NOT NULL DEFAULT 10,
                last_checked_at TEXT,
                next_check_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                initial_sync_completed INTEGER NOT NULL DEFAULT 0,
                history_sync_status TEXT NOT NULL DEFAULT 'idle'
                    CHECK (history_sync_status IN ('idle', 'pending', 'running', 'paused', 'completed', 'failed')),
                history_next_cursor INTEGER NOT NULL DEFAULT 0,
                history_has_more INTEGER NOT NULL DEFAULT 1,
                history_processed_pages INTEGER NOT NULL DEFAULT 0,
                history_scanned_items INTEGER NOT NULL DEFAULT 0,
                history_new_videos INTEGER NOT NULL DEFAULT 0,
                history_started_at TEXT,
                history_updated_at TEXT,
                history_completed_at TEXT,
                history_last_error TEXT,
                history_cursor_history TEXT NOT NULL DEFAULT '[0]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (check_interval_minutes > 0),
                CHECK (consecutive_failures >= 0)
            );

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aweme_id TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                description TEXT NOT NULL DEFAULT '',
                hashtags TEXT NOT NULL DEFAULT '[]',
                publish_time TEXT,
                video_url TEXT NOT NULL DEFAULT '',
                cover_url TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}',
                display_title TEXT,
                text_sources TEXT NOT NULL DEFAULT '{}',
                is_processed INTEGER NOT NULL DEFAULT 0,
                needs_review INTEGER NOT NULL DEFAULT 0,
                classification_status TEXT NOT NULL DEFAULT 'ignored'
                    CHECK (classification_status IN ('matched', 'ignored', 'review')),
                parser_confidence REAL,
                parsed_show_title TEXT,
                parsed_season_number INTEGER NOT NULL DEFAULT 1,
                parsed_episode_number INTEGER,
                parser_method TEXT,
                parser_reason TEXT,
                show_title_candidate TEXT,
                season_candidate INTEGER NOT NULL DEFAULT 1,
                episode_candidate INTEGER,
                content_type TEXT NOT NULL DEFAULT 'unknown'
                    CHECK (content_type IN ('episode', 'trailer', 'show_content', 'unknown', 'non_drama')),
                parser_evidence TEXT NOT NULL DEFAULT '{}',
                llm_raw_result TEXT,
                ocr_text TEXT,
                ocr_confidence REAL,
                ocr_processed_at TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS shows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                normalized_title TEXT NOT NULL UNIQUE,
                aliases TEXT NOT NULL DEFAULT '[]',
                latest_season INTEGER,
                latest_episode INTEGER,
                latest_update_at TEXT,
                expected_episode_count INTEGER,
                is_ignored INTEGER NOT NULL DEFAULT 0,
                ignored_at TEXT,
                ignore_reason TEXT,
                is_following INTEGER NOT NULL DEFAULT 0,
                followed_at TEXT,
                status TEXT NOT NULL DEFAULT 'updating',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (status IN ('updating', 'completed', 'paused')),
                CHECK (expected_episode_count IS NULL OR expected_episode_count > 0),
                CHECK (is_ignored IN (0, 1)),
                CHECK (is_following IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                season_number INTEGER NOT NULL DEFAULT 1,
                episode_number INTEGER NOT NULL,
                first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
                first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                published_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(show_id, season_number, episode_number),
                CHECK (season_number >= 1),
                CHECK (episode_number >= 0)
            );

            CREATE TABLE IF NOT EXISTS episode_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                published_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(episode_id, video_id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                channel TEXT NOT NULL,
                success INTEGER NOT NULL,
                error TEXT,
                sent_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL DEFAULT 0, trigger_type TEXT NOT NULL,
                success INTEGER NOT NULL, error_type TEXT, error_message TEXT,
                fetched_videos INTEGER NOT NULL DEFAULT 0, new_videos INTEGER NOT NULL DEFAULT 0,
                duplicate_videos INTEGER NOT NULL DEFAULT 0, matched_videos INTEGER NOT NULL DEFAULT 0,
                review_videos INTEGER NOT NULL DEFAULT 0, ignored_videos INTEGER NOT NULL DEFAULT 0,
                new_episodes INTEGER NOT NULL DEFAULT 0, llm_calls INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS update_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                season_number INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL DEFAULT 'new_episode',
                occurred_at TEXT NOT NULL,
                read_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(episode_id, event_type)
            );

            CREATE TABLE IF NOT EXISTS manual_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                old_value TEXT NOT NULL DEFAULT '{}',
                new_value TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_accounts_due
                ON accounts(enabled, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_videos_account_created
                ON videos(account_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_review
                ON videos(needs_review, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_content_created
                ON videos(content_type, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_parser_created
                ON videos(parser_method, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_published
                ON videos(COALESCE(publish_time, created_at) DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_account_published
                ON videos(account_id, COALESCE(publish_time, created_at) DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_classification_published
                ON videos(classification_status, COALESCE(publish_time, created_at) DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_show_number
                ON episodes(show_id, episode_number DESC);
            CREATE INDEX IF NOT EXISTS idx_episode_sources_episode_published
                ON episode_sources(
                    episode_id,
                    COALESCE(published_at, created_at) ASC,
                    id ASC
                );
            CREATE INDEX IF NOT EXISTS idx_notifications_episode
                ON notifications(episode_id, sent_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_runs_account_started
                ON scan_runs(account_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_runs_account_order
                ON scan_runs(account_id, started_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_runs_started ON scan_runs(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_update_events_occurred
                ON update_events(occurred_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_update_events_unread
                ON update_events(read_at, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_update_events_unread_order
                ON update_events(read_at, occurred_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_update_events_show
                ON update_events(show_id, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_update_events_show_order
                ON update_events(show_id, occurred_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_manual_corrections_created
                ON manual_corrections(created_at DESC, id DESC);
            """
        )

    def _migrate_schema(self, connection: sqlite3.Connection, previous_version: int) -> None:
        """Apply additive migrations without requiring users to recreate SQLite data."""
        if not _table_has_column(connection, "episodes", "season_number"):
            _migrate_episode_seasons(connection)
        elif not _episodes_allow_zero(connection):
            _migrate_episodes_allow_zero(connection)
        connection.execute("DROP INDEX IF EXISTS idx_episodes_show_number")
        connection.execute(
            "CREATE INDEX idx_episodes_show_number "
            "ON episodes(show_id, season_number DESC, episode_number DESC)"
        )
        if not _table_has_column(connection, "videos", "classification_status"):
            connection.execute(
                "ALTER TABLE videos ADD COLUMN classification_status TEXT NOT NULL DEFAULT 'ignored'"
            )
        if not _table_has_column(connection, "videos", "parser_reason"):
            connection.execute("ALTER TABLE videos ADD COLUMN parser_reason TEXT")
        for column, definition in {
            "show_title_candidate": "TEXT",
            "episode_candidate": "INTEGER",
            "content_type": "TEXT NOT NULL DEFAULT 'unknown'",
            "display_title": "TEXT",
            "text_sources": "TEXT NOT NULL DEFAULT '{}'",
            "parser_evidence": "TEXT NOT NULL DEFAULT '{}'",
            "llm_raw_result": "TEXT",
            "parsed_season_number": "INTEGER NOT NULL DEFAULT 1",
            "season_candidate": "INTEGER NOT NULL DEFAULT 1",
            "ocr_text": "TEXT",
            "ocr_confidence": "REAL",
            "ocr_processed_at": "TEXT",
        }.items():
            if not _table_has_column(connection, "videos", column):
                connection.execute(f"ALTER TABLE videos ADD COLUMN {column} {definition}")
        for column, definition in {
            "history_sync_status": "TEXT NOT NULL DEFAULT 'idle'",
            "history_next_cursor": "INTEGER NOT NULL DEFAULT 0",
            "history_has_more": "INTEGER NOT NULL DEFAULT 1",
            "history_processed_pages": "INTEGER NOT NULL DEFAULT 0",
            "history_scanned_items": "INTEGER NOT NULL DEFAULT 0",
            "history_new_videos": "INTEGER NOT NULL DEFAULT 0",
            "history_started_at": "TEXT",
            "history_updated_at": "TEXT",
            "history_completed_at": "TEXT",
            "history_last_error": "TEXT",
            "history_cursor_history": "TEXT NOT NULL DEFAULT '[0]'",
        }.items():
            if not _table_has_column(connection, "accounts", column):
                connection.execute(f"ALTER TABLE accounts ADD COLUMN {column} {definition}")
        for column, definition in {
            "expected_episode_count": "INTEGER",
            "is_ignored": "INTEGER NOT NULL DEFAULT 0",
            "ignored_at": "TEXT",
            "ignore_reason": "TEXT",
            "latest_season": "INTEGER",
            "is_following": "INTEGER NOT NULL DEFAULT 0",
            "followed_at": "TEXT",
        }.items():
            if not _table_has_column(connection, "shows", column):
                connection.execute(f"ALTER TABLE shows ADD COLUMN {column} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_shows_quality_stale "
            "ON shows(is_ignored, status, latest_update_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_classification "
            "ON videos(classification_status, created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_accounts_history_sync "
            "ON accounts(history_sync_status, history_updated_at)"
        )
        if previous_version >= SCHEMA_VERSION:
            return
        if previous_version < 3:
            connection.execute(
                """
                UPDATE videos
                SET classification_status = CASE
                        WHEN needs_review = 1 THEN 'review'
                        WHEN parsed_show_title IS NOT NULL AND parsed_episode_number IS NOT NULL THEN 'matched'
                        ELSE 'ignored'
                    END,
                    parser_reason = CASE
                        WHEN needs_review = 1 THEN 'legacy_review'
                        WHEN parsed_show_title IS NOT NULL AND parsed_episode_number IS NOT NULL THEN 'legacy_matched'
                        ELSE 'legacy_ignored'
                    END
                """
            )

    def _repair_placeholder_account_nicknames(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT id, nickname, sec_uid FROM accounts").fetchall()
        for row in rows:
            if not _is_placeholder_nickname(row["nickname"]):
                continue
            connection.execute(
                "UPDATE accounts SET nickname = ?, updated_at = ? WHERE id = ?",
                (_fallback_account_nickname(str(row["sec_uid"])), utc_now(), str(row["id"])),
            )

    def _get_meta(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def _set_meta(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO app_meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _import_legacy_state(self, connection: sqlite3.Connection) -> None:
        if self.legacy_state_path is None or not self.legacy_state_path.is_file():
            return
        try:
            raw_state = json.loads(self.legacy_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw_state, Mapping):
            return
        raw_users = raw_state.get("users")
        if not isinstance(raw_users, list):
            return
        for raw_user in raw_users:
            if not isinstance(raw_user, Mapping):
                continue
            self._import_legacy_user(connection, raw_user)

    def _import_legacy_user(self, connection: sqlite3.Connection, raw_user: Mapping[str, Any]) -> None:
        sec_uid = str(raw_user.get("sec_user_id") or raw_user.get("sec_uid") or "").strip()
        if not sec_uid:
            return
        account_id = str(raw_user.get("id") or uuid.uuid4())
        now = utc_now()
        nickname = _safe_account_nickname(raw_user.get("nickname"), sec_uid)
        homepage_url = str(raw_user.get("profile_url") or raw_user.get("homepage_url") or "").strip()
        created_at = str(raw_user.get("created_at") or now)
        updated_at = str(raw_user.get("updated_at") or now)
        connection.execute(
            """
            INSERT INTO accounts(
                id, nickname, sec_uid, homepage_url, enabled, check_interval_minutes,
                last_checked_at, next_check_at, last_success_at, last_error,
                consecutive_failures, initial_sync_completed, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 1, ?, ?)
            ON CONFLICT(sec_uid) DO NOTHING
            """,
            (
                account_id,
                nickname,
                sec_uid,
                homepage_url,
                int(bool(raw_user.get("enabled", True))),
                _positive_int(raw_user.get("check_interval_minutes"), 10),
                _optional_text(raw_user.get("last_checked_at")),
                _optional_text(raw_user.get("last_checked_at")),
                _optional_text(raw_user.get("last_error")),
                created_at,
                updated_at,
            ),
        )
        account_row = connection.execute(
            "SELECT id FROM accounts WHERE sec_uid = ?", (sec_uid,)
        ).fetchone()
        if account_row is None:
            return
        imported_account_id = str(account_row["id"])
        records_by_aweme = _legacy_download_records(raw_user.get("download_records"))
        raw_ids = raw_user.get("downloaded_aweme_ids")
        if not isinstance(raw_ids, list):
            return
        for raw_aweme_id in raw_ids:
            aweme_id = str(raw_aweme_id or "").strip()
            if not aweme_id:
                continue
            record = records_by_aweme.get(aweme_id, {})
            connection.execute(
                """
                INSERT INTO videos(
                    aweme_id, account_id, description, hashtags, publish_time, video_url,
                    cover_url, raw_json, is_processed, needs_review, parser_confidence,
                    created_at, processed_at
                ) VALUES (?, ?, ?, '[]', ?, '', NULL, '{}', 1, 0, NULL, ?, ?)
                ON CONFLICT(aweme_id) DO NOTHING
                """,
                (
                    aweme_id,
                    imported_account_id,
                    str(record.get("desc") or "").strip(),
                    _optional_text(record.get("publish_time")),
                    str(record.get("downloaded_at") or created_at),
                    str(record.get("downloaded_at") or created_at),
                ),
            )

    def create_account(
        self,
        *,
        sec_uid: str,
        nickname: str,
        homepage_url: str,
        check_interval_minutes: int = 10,
        enabled: bool = True,
        initial_sync_completed: bool = False,
    ) -> dict[str, Any]:
        safe_sec_uid = sec_uid.strip()
        if not safe_sec_uid:
            raise ValueError("sec_uid 不能为空")
        safe_nickname = _safe_account_nickname(nickname, safe_sec_uid)
        now = utc_now()
        account_id = str(uuid.uuid4())
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO accounts(
                    id, nickname, sec_uid, homepage_url, enabled, check_interval_minutes,
                    initial_sync_completed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    safe_nickname,
                    safe_sec_uid,
                    homepage_url.strip(),
                    int(enabled),
                    _positive_int(check_interval_minutes, 10),
                    int(initial_sync_completed),
                    now,
                    now,
                ),
            )
            return self._require_account(connection, account_id)

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            return _account_row(row) if row else None

    def get_account_by_sec_uid(self, sec_uid: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE sec_uid = ?", (sec_uid,)).fetchone()
            return _account_row(row) if row else None

    def list_accounts(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM accounts"
        params: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY nickname COLLATE NOCASE, created_at"
        with self._transaction() as connection:
            rows = connection.execute(query, params).fetchall()
            return [_account_row(row) for row in rows]

    def update_account(self, account_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"nickname", "homepage_url", "enabled", "check_interval_minutes", "next_check_at"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            existing = self.get_account(account_id)
            if existing is None:
                raise KeyError("账号不存在")
            return existing
        if "nickname" in values:
            values["nickname"] = str(values["nickname"] or "").strip()
            if not values["nickname"]:
                raise ValueError("昵称不能为空")
        if "homepage_url" in values:
            values["homepage_url"] = str(values["homepage_url"] or "").strip()
        if "enabled" in values:
            values["enabled"] = int(bool(values["enabled"]))
        if "check_interval_minutes" in values:
            values["check_interval_minutes"] = _positive_int(values["check_interval_minutes"], 10)
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"UPDATE accounts SET {assignments} WHERE id = ?",
                (*values.values(), account_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def start_history_backfill(self, account_id: str) -> dict[str, Any]:
        """Reset persisted progress so a user can begin a fresh, idempotent scan."""
        now = utc_now()
        return self.update_history_sync_state(
            account_id,
            status="pending",
            next_cursor=0,
            has_more=True,
            processed_pages=0,
            scanned_items=0,
            new_videos=0,
            started_at=now,
            updated_at=now,
            completed_at=None,
            last_error=None,
            cursor_history=[0],
        )

    def pause_history_backfill(self, account_id: str) -> dict[str, Any]:
        account = self.get_account(account_id)
        if account is None:
            raise KeyError("账号不存在")
        if account["history_sync_status"] not in {"pending", "running"}:
            raise ValueError("当前历史补全不能暂停")
        history = account["history_sync"]
        return self.update_history_sync_state(
            account_id,
            status="paused",
            next_cursor=int(history["next_cursor"]),
            has_more=bool(history["has_more"]),
            processed_pages=int(history["processed_pages"]),
            scanned_items=int(history["scanned_items"]),
            new_videos=int(history["new_videos"]),
            started_at=history["started_at"],
            completed_at=history["completed_at"],
            last_error=history["last_error"],
        )

    def resume_history_backfill(self, account_id: str) -> dict[str, Any]:
        account = self.get_account(account_id)
        if account is None:
            raise KeyError("账号不存在")
        if account["history_sync_status"] not in {"paused", "failed"}:
            raise ValueError("当前历史补全不能继续")
        history = account["history_sync"]
        if not history["has_more"]:
            raise ValueError("历史补全已完成，请重新开始")
        return self.update_history_sync_state(
            account_id,
            status="pending",
            next_cursor=int(history["next_cursor"]),
            has_more=True,
            processed_pages=int(history["processed_pages"]),
            scanned_items=int(history["scanned_items"]),
            new_videos=int(history["new_videos"]),
            started_at=history["started_at"] or utc_now(),
            completed_at=None,
            last_error=None,
            cursor_history=history["cursor_history"],
        )

    def update_history_sync_state(
        self,
        account_id: str,
        *,
        status: str,
        next_cursor: int,
        has_more: bool,
        processed_pages: int,
        scanned_items: int,
        new_videos: int,
        started_at: str | None,
        updated_at: str | None = None,
        completed_at: str | None = None,
        last_error: str | None = None,
        cursor_history: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        if status not in HISTORY_SYNC_STATUSES:
            raise ValueError("历史同步状态无效")
        now = updated_at or utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts SET
                    history_sync_status = ?, history_next_cursor = ?, history_has_more = ?,
                    history_processed_pages = ?, history_scanned_items = ?, history_new_videos = ?,
                    history_started_at = ?, history_updated_at = ?, history_completed_at = ?,
                    history_last_error = ?, history_cursor_history = COALESCE(?, history_cursor_history),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    _non_negative_int(next_cursor),
                    int(bool(has_more)),
                    _non_negative_int(processed_pages),
                    _non_negative_int(scanned_items),
                    _non_negative_int(new_videos),
                    _optional_text(started_at),
                    now,
                    _optional_text(completed_at),
                    _optional_text(last_error),
                    _json_cursor_history(cursor_history) if cursor_history is not None else None,
                    now,
                    account_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def mark_history_backfill_running(self, account_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET history_sync_status = 'running', history_updated_at = ?,
                    history_started_at = COALESCE(history_started_at, ?), updated_at = ?
                WHERE id = ? AND history_sync_status = 'pending' AND history_has_more = 1
                """,
                (now, now, now, account_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
                if row is None:
                    raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def advance_history_backfill_page(
        self,
        account_id: str,
        *,
        expected_cursor: int,
        next_cursor: int,
        has_more: bool,
        scanned_count: int,
        new_video_count: int,
        cursor_history: Sequence[int],
    ) -> dict[str, Any]:
        now = utc_now()
        desired_status = "running" if has_more else "completed"
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts SET
                    history_sync_status = CASE
                        WHEN ? = 0 THEN 'completed'
                        WHEN history_sync_status = 'paused' THEN 'paused'
                        ELSE ?
                    END,
                    history_next_cursor = ?, history_has_more = ?,
                    history_processed_pages = history_processed_pages + 1,
                    history_scanned_items = history_scanned_items + ?,
                    history_new_videos = history_new_videos + ?,
                    history_cursor_history = ?,
                    history_started_at = COALESCE(history_started_at, ?),
                    history_updated_at = ?,
                    history_completed_at = CASE WHEN ? = 0 THEN ? ELSE NULL END,
                    history_last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND history_next_cursor = ?
                  AND history_sync_status IN ('pending', 'running', 'paused')
                """,
                (
                    int(bool(has_more)),
                    desired_status,
                    _non_negative_int(next_cursor),
                    int(bool(has_more)),
                    _non_negative_int(scanned_count),
                    _non_negative_int(new_video_count),
                    _json_cursor_history(cursor_history),
                    now,
                    now,
                    int(bool(has_more)),
                    now,
                    now,
                    account_id,
                    _non_negative_int(expected_cursor),
                ),
            )
            if cursor.rowcount == 0:
                raise RuntimeError("历史补全状态或游标已被其他任务更新")
            return self._require_account(connection, account_id)

    def fail_history_backfill(self, account_id: str, *, error: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET history_sync_status = 'failed', history_last_error = ?,
                    history_updated_at = ?, history_completed_at = NULL, updated_at = ?
                WHERE id = ? AND history_sync_status IN ('pending', 'running')
                """,
                (_optional_text(error), now, now, account_id),
            )
            if cursor.rowcount == 0:
                row = connection.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
                if row is None:
                    raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def recover_running_history_backfills(self) -> int:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET history_sync_status = 'pending', history_updated_at = ?, updated_at = ?
                WHERE history_sync_status = 'running' AND history_has_more = 1
                """,
                (now, now),
            )
            return int(cursor.rowcount)

    def list_active_history_backfills(self, *, limit: int = 500) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM accounts
                WHERE history_sync_status IN ('pending', 'running') AND history_has_more = 1
                ORDER BY COALESCE(history_updated_at, created_at), created_at
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            results = [_account_row(row) for row in rows]
            for result in results:
                result["recent_scan_runs"] = self._list_scan_runs(connection, str(result["id"]), 20)
            return results

    def record_scan_run(self, account_id: str, **values: Any) -> dict[str, Any]:
        now = utc_now()
        fields = ("started_at", "finished_at", "duration_ms", "trigger_type", "success", "error_type", "error_message", "fetched_videos", "new_videos", "duplicate_videos", "matched_videos", "review_videos", "ignored_videos", "new_episodes", "llm_calls")
        data: dict[str, Any] = {key: 0 for key in fields}
        data.update(started_at=now, finished_at=now, trigger_type="scheduler", success=0)
        data.update(values)
        data["error_message"] = str(data.get("error_message") or "")[:2000] or None
        with self._transaction() as connection:
            cursor = connection.execute(
                f"INSERT INTO scan_runs(account_id,{','.join(fields)},created_at) VALUES ({','.join('?' for _ in range(len(fields)+2))})",
                (account_id, *(data[key] for key in fields), now),
            )
            return dict(connection.execute("SELECT * FROM scan_runs WHERE id=?", (cursor.lastrowid,)).fetchone())

    def _list_scan_runs(self, connection: sqlite3.Connection, account_id: str, limit: int) -> list[dict[str, Any]]:
        rows = connection.execute("SELECT * FROM scan_runs WHERE account_id=? ORDER BY started_at DESC,id DESC LIMIT ?", (account_id, max(1, min(int(limit), 100)))).fetchall()
        return [dict(row) for row in rows]

    def list_scan_runs(self, account_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return self._list_scan_runs(connection, account_id, limit)

    def prune_scan_runs(self, *, retention_days: int) -> int:
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - max(1, retention_days) * 86400, timezone.utc).isoformat(timespec="seconds")
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM scan_runs WHERE created_at < ?", (cutoff,))
            return int(cursor.rowcount)

    def delete_account(self, account_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            account = self._require_account(connection, account_id)

            # Videos cascade with the account. Repair episodes that use one of
            # those videos as their canonical first source before that happens.
            affected_rows = connection.execute(
                """
                SELECT DISTINCT episodes.id, episodes.show_id
                FROM episodes
                WHERE episodes.first_account_id = ?
                   OR episodes.first_video_id IN (
                       SELECT id FROM videos WHERE account_id = ?
                   )
                """,
                (account_id, account_id),
            ).fetchall()
            affected_show_ids = {int(row["show_id"]) for row in affected_rows}

            for row in affected_rows:
                alternate_source = connection.execute(
                    """
                    SELECT episode_sources.video_id, episode_sources.account_id,
                           episode_sources.published_at
                    FROM episode_sources
                    JOIN videos ON videos.id = episode_sources.video_id
                    WHERE episode_sources.episode_id = ?
                      AND episode_sources.account_id != ?
                      AND videos.account_id != ?
                    ORDER BY COALESCE(episode_sources.published_at, episode_sources.created_at) ASC,
                             episode_sources.id ASC
                    LIMIT 1
                    """,
                    (int(row["id"]), account_id, account_id),
                ).fetchone()
                if alternate_source is None:
                    connection.execute("DELETE FROM episodes WHERE id = ?", (int(row["id"]),))
                    continue
                connection.execute(
                    """
                    UPDATE episodes
                    SET first_video_id = ?, first_account_id = ?, published_at = ?
                    WHERE id = ?
                    """,
                    (
                        int(alternate_source["video_id"]),
                        str(alternate_source["account_id"]),
                        _optional_text(alternate_source["published_at"]),
                        int(row["id"]),
                    ),
                )

            # Remove source rows explicitly so the account's restrictive source
            # foreign key cannot interfere with the videos' cascade deletion.
            connection.execute("DELETE FROM episode_sources WHERE account_id = ?", (account_id,))
            connection.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

            self._refresh_show_latest(connection, affected_show_ids)
            return account

    def _refresh_show_latest(
        self, connection: sqlite3.Connection, show_ids: set[int]
    ) -> int:
        """Refresh cached metadata from each show's highest numbered episode."""
        if not show_ids:
            return 0
        now = utc_now()
        refreshed = 0
        for show_id in show_ids:
            latest = connection.execute(
                """
                SELECT season_number, episode_number,
                       COALESCE(published_at, created_at) AS latest_update_at
                FROM episodes
                WHERE show_id = ?
                ORDER BY season_number DESC, episode_number DESC,
                         COALESCE(published_at, created_at) DESC, id DESC
                LIMIT 1
                """,
                (show_id,),
            ).fetchone()
            current = connection.execute(
                "SELECT latest_season, latest_episode, latest_update_at FROM shows WHERE id = ?", (show_id,)
            ).fetchone()
            if current is None:
                continue
            if latest is None:
                if current["latest_season"] is None and current["latest_episode"] is None and current["latest_update_at"] is None:
                    continue
                connection.execute(
                    """
                    UPDATE shows
                    SET latest_season = NULL, latest_episode = NULL,
                        latest_update_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, show_id),
                )
                refreshed += 1
                continue
            latest_season = int(latest["season_number"])
            latest_episode = int(latest["episode_number"])
            latest_update_at = latest["latest_update_at"]
            if (
                current["latest_season"] == latest_season
                and current["latest_episode"] == latest_episode
                and current["latest_update_at"] == latest_update_at
            ):
                continue
            connection.execute(
                """
                UPDATE shows
                SET latest_season = ?, latest_episode = ?, latest_update_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (latest_season, latest_episode, latest_update_at, now, show_id),
            )
            refreshed += 1
        return refreshed

    def _repair_episode_first_source(
        self, connection: sqlite3.Connection, episode_id: int
    ) -> bool:
        """Make an episode's canonical source point at its earliest known publication."""
        episode = connection.execute(
            """
            SELECT id, first_video_id, first_account_id, published_at
            FROM episodes
            WHERE id = ?
            """,
            (episode_id,),
        ).fetchone()
        if episode is None:
            return False

        source_rows = connection.execute(
            """
            SELECT episode_sources.id, episode_sources.video_id, episode_sources.account_id,
                   episode_sources.published_at AS source_published_at,
                   videos.publish_time AS video_published_at,
                   episode_sources.created_at
            FROM episode_sources
            JOIN videos ON videos.id = episode_sources.video_id
            WHERE episode_sources.episode_id = ?
            """,
            (episode_id,),
        ).fetchall()
        if not source_rows:
            return False

        earliest = min(source_rows, key=_episode_source_order_key)
        published_at = _episode_source_published_at(earliest)
        if (
            int(episode["first_video_id"]) == int(earliest["video_id"])
            and str(episode["first_account_id"]) == str(earliest["account_id"])
            and _optional_text(episode["published_at"]) == published_at
        ):
            return False
        connection.execute(
            """
            UPDATE episodes
            SET first_video_id = ?, first_account_id = ?, published_at = ?
            WHERE id = ?
            """,
            (
                int(earliest["video_id"]),
                str(earliest["account_id"]),
                published_at,
                episode_id,
            ),
        )
        return True

    def create_video(
        self,
        *,
        aweme_id: str,
        account_id: str,
        description: str,
        hashtags: Sequence[str],
        publish_time: str | None,
        video_url: str,
        cover_url: str | None,
        raw: Mapping[str, Any],
        display_title: str | None = None,
        text_sources: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        safe_aweme_id = aweme_id.strip()
        if not safe_aweme_id:
            raise ValueError("aweme_id 不能为空")
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO videos(
                    aweme_id, account_id, description, hashtags, publish_time, video_url,
                    cover_url, raw_json, display_title, text_sources, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_aweme_id,
                    account_id,
                    description.strip(),
                    _json_array(hashtags),
                    _optional_text(publish_time),
                    video_url.strip(),
                    _optional_text(cover_url),
                    json.dumps(raw, ensure_ascii=False, sort_keys=True),
                    _optional_text(display_title),
                    _json_text_sources(text_sources),
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM videos WHERE aweme_id = ?", (safe_aweme_id,)).fetchone()
            if row is None:
                raise RuntimeError("保存视频后无法读取记录")
            return _video_row(row), cursor.rowcount == 1

    def update_video_text_metadata(
        self,
        video_id: int,
        *,
        display_title: str | None,
        text_sources: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE videos
                SET display_title = ?, text_sources = ?
                WHERE id = ?
                """,
                (
                    _optional_text(display_title),
                    _json_text_sources(text_sources),
                    video_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("视频不存在")
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return _video_row(row)

    def save_video_ocr(self, video_id: int, *, text: str | None, confidence: float | None) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute("UPDATE videos SET ocr_text=?,ocr_confidence=?,ocr_processed_at=? WHERE id=?", (_optional_text(text), confidence, utc_now(), video_id))
            if cursor.rowcount == 0:
                raise KeyError("视频不存在")
            return _video_row(connection.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone())

    def refresh_video_metadata(
        self,
        video_id: int,
        *,
        description: str,
        video_url: str,
        cover_url: str | None,
        raw: Mapping[str, Any],
        display_title: str | None,
        text_sources: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Merge richer provider metadata without erasing persisted values.

        This deliberately updates source metadata only. Parser state and episode
        archives remain unchanged until the pipeline explicitly reparses an
        eligible ignored/review video.
        """
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                raise KeyError("视频不存在")
            existing = _video_row(row)
            existing_raw = _json_object(existing.get("raw_json"))
            merged_raw = _merge_richer_json(existing_raw, raw)
            merged_sources = _merge_text_sources(existing.get("text_sources"), text_sources)
            merged_description = _prefer_richer_text(existing.get("description"), description)
            merged_display_title = _prefer_richer_text(existing.get("display_title"), display_title)
            merged_video_url = _prefer_non_empty(existing.get("video_url"), video_url)
            merged_cover_url = _prefer_non_empty(existing.get("cover_url"), cover_url)
            merged_raw_json = json.dumps(merged_raw, ensure_ascii=False, sort_keys=True)
            changed = any(
                (
                    merged_description != existing.get("description"),
                    merged_display_title != existing.get("display_title"),
                    merged_video_url != existing.get("video_url"),
                    merged_cover_url != existing.get("cover_url"),
                    merged_raw != existing_raw,
                    merged_sources != existing.get("text_sources"),
                )
            )
            if not changed:
                return existing, False
            connection.execute(
                """
                UPDATE videos SET
                    description = ?, video_url = ?, cover_url = ?, raw_json = ?,
                    display_title = ?, text_sources = ?
                WHERE id = ?
                """,
                (
                    merged_description or "",
                    merged_video_url or "",
                    merged_cover_url,
                    merged_raw_json,
                    merged_display_title,
                    _json_text_sources(merged_sources),
                    video_id,
                ),
            )
            updated = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if updated is None:
                raise RuntimeError("更新视频 metadata 后无法读取记录")
            return _video_row(updated), True

    def get_video(self, video_id: int) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return _video_row(row) if row else None

    def get_video_by_aweme_id(self, aweme_id: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM videos WHERE aweme_id = ?", (aweme_id,)).fetchone()
            return _video_row(row) if row else None

    def update_video_processing(
        self,
        video_id: int,
        *,
        is_processed: bool,
        needs_review: bool,
        parser_confidence: float | None,
        parsed_show_title: str | None = None,
        parsed_season_number: int = 1,
        parsed_episode_number: int | None = None,
        parser_method: str | None = None,
        classification_status: str | None = None,
        parser_reason: str | None = None,
        show_title_candidate: str | None = None,
        season_candidate: int = 1,
        episode_candidate: int | None = None,
        content_type: str = "unknown",
        parser_evidence: Mapping[str, Any] | None = None,
        llm_raw_result: Any | None = None,
    ) -> dict[str, Any]:
        resolved_status = classification_status or _classification_from_legacy_fields(
            needs_review=needs_review,
            parsed_show_title=parsed_show_title,
            parsed_episode_number=parsed_episode_number,
        )
        _validate_video_classification(resolved_status)
        _validate_video_content_type(content_type)
        if parsed_episode_number is not None and int(parsed_episode_number) < 0:
            raise ValueError("集数不能小于 0")
        if int(parsed_season_number) < 1 or int(season_candidate) < 1:
            raise ValueError("季数不能小于 1")
        if episode_candidate is not None and int(episode_candidate) < 0:
            raise ValueError("候选集数不能小于 0")
        if bool(needs_review) != (resolved_status == "review"):
            raise ValueError("needs_review 必须与分类状态一致")
        if bool(is_processed) == (resolved_status == "review"):
            raise ValueError("review 视频不能标记为已处理，其他状态必须标记为已处理")
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE videos SET
                    is_processed = ?, needs_review = ?, classification_status = ?, parser_confidence = ?,
                    parsed_show_title = ?, parsed_season_number = ?, parsed_episode_number = ?, parser_method = ?,
                    parser_reason = ?, show_title_candidate = ?, season_candidate = ?, episode_candidate = ?, content_type = ?,
                    parser_evidence = COALESCE(?, parser_evidence),
                    llm_raw_result = COALESCE(?, llm_raw_result),
                    processed_at = ?
                WHERE id = ?
                """,
                (
                    int(is_processed),
                    int(needs_review),
                    resolved_status,
                    parser_confidence,
                    _optional_text(parsed_show_title),
                    parsed_season_number,
                    parsed_episode_number,
                    _optional_text(parser_method),
                    _optional_text(parser_reason),
                    _optional_text(show_title_candidate),
                    season_candidate,
                    episode_candidate,
                    content_type,
                    _json_mapping(parser_evidence) if parser_evidence is not None else None,
                    json.dumps(llm_raw_result, ensure_ascii=False, separators=(",", ":"))
                    if llm_raw_result is not None
                    else None,
                    utc_now() if is_processed else None,
                    video_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("视频不存在")
            row = connection.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            return _video_row(row)

    def list_videos(
        self,
        *,
        needs_review: bool | None = None,
        classification_status: str | None = None,
        account_id: str | None = None,
        parser_reason: str | None = None,
        exclude_video_id: int | None = None,
        oldest_first: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if classification_status is not None:
            _validate_video_classification(classification_status)
        safe_limit = max(1, min(int(limit), 500))
        query = """
            SELECT videos.*, accounts.nickname AS account_nickname, accounts.sec_uid AS account_sec_uid
            FROM videos JOIN accounts ON accounts.id = videos.account_id
        """
        params: list[Any] = []
        clauses: list[str] = []
        if needs_review is not None:
            clauses.append("videos.needs_review = ?")
            params.append(int(needs_review))
        if classification_status is not None:
            clauses.append("videos.classification_status = ?")
            params.append(classification_status)
        if account_id is not None:
            clauses.append("videos.account_id = ?")
            params.append(str(account_id))
        if parser_reason is not None:
            clauses.append("videos.parser_reason = ?")
            params.append(str(parser_reason))
        if exclude_video_id is not None:
            clauses.append("videos.id != ?")
            params.append(int(exclude_video_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += (
            " ORDER BY COALESCE(videos.publish_time, videos.created_at) "
            + ("ASC" if oldest_first else "DESC")
            + " LIMIT ?"
        )
        params.append(safe_limit)
        with self._transaction() as connection:
            return [_video_row(row) for row in connection.execute(query, params).fetchall()]

    def list_recent_account_videos(
        self,
        account_id: str,
        *,
        limit: int = 20,
        exclude_video_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return a bounded context window without leaking repository internals."""
        return self.list_videos(
            account_id=account_id,
            exclude_video_id=exclude_video_id,
            limit=limit,
        )

    def list_recent_account_matches(
        self,
        account_id: str,
        *,
        limit: int = 20,
        exclude_video_id: int | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        clauses = ["episode_sources.account_id = ?"]
        params: list[Any] = [str(account_id)]
        if exclude_video_id is not None:
            clauses.append("videos.id != ?")
            params.append(int(exclude_video_id))
        query = """
            SELECT videos.*, shows.id AS show_id, shows.title AS show_title,
                   shows.aliases AS show_aliases, episodes.episode_number AS episode_number
            FROM episode_sources
            JOIN episodes ON episodes.id = episode_sources.episode_id
            JOIN shows ON shows.id = episodes.show_id
            JOIN videos ON videos.id = episode_sources.video_id
            WHERE """ + " AND ".join(clauses) + """
            ORDER BY COALESCE(videos.publish_time, episode_sources.published_at, videos.created_at) DESC,
                     videos.id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self._transaction() as connection:
            results: list[dict[str, Any]] = []
            for row in connection.execute(query, params).fetchall():
                result = _video_row(row)
                result["show_id"] = int(row["show_id"])
                result["show_title"] = str(row["show_title"])
                result["aliases"] = _json_list(row["show_aliases"])
                result["episode_number"] = int(row["episode_number"])
                results.append(result)
            return results

    def list_account_show_candidates(
        self,
        account_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT shows.*, COUNT(DISTINCT episodes.id) AS matched_episode_count,
                       MAX(episodes.episode_number) AS latest_account_episode,
                       MAX(COALESCE(episode_sources.published_at, videos.publish_time, videos.created_at))
                           AS latest_account_update_at
                FROM shows
                JOIN episodes ON episodes.show_id = shows.id
                JOIN episode_sources ON episode_sources.episode_id = episodes.id
                JOIN videos ON videos.id = episode_sources.video_id
                WHERE episode_sources.account_id = ?
                GROUP BY shows.id
                ORDER BY latest_account_update_at DESC, matched_episode_count DESC, shows.id DESC
                LIMIT ?
                """,
                (str(account_id), safe_limit),
            ).fetchall()
            return [_show_row(row) for row in rows]

    def list_reparse_videos(self, account_id: str, *, scope: str) -> list[dict[str, Any]]:
        filters = {
            "legacy_ignored": "videos.parser_reason = 'legacy_ignored'",
            "ignored": "videos.classification_status = 'ignored'",
            "ignored_review": "videos.classification_status IN ('ignored', 'review')",
        }
        try:
            scope_filter = filters[scope]
        except KeyError as exc:
            raise ValueError("重新解析范围无效") from exc
        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT videos.*, accounts.nickname AS account_nickname, accounts.sec_uid AS account_sec_uid
                FROM videos JOIN accounts ON accounts.id = videos.account_id
                WHERE videos.account_id = ? AND {scope_filter}
                ORDER BY COALESCE(videos.publish_time, videos.created_at) ASC, videos.id ASC
                LIMIT 5000
                """,
                (str(account_id),),
            ).fetchall()
            return [_video_row(row) for row in rows]

    def ignore_review_videos(self, video_ids: Sequence[int]) -> int:
        safe_ids = _video_ids(video_ids)
        if not safe_ids:
            return 0
        placeholders = ", ".join("?" for _ in safe_ids)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"""
                UPDATE videos
                SET is_processed = 1,
                    needs_review = 0,
                    classification_status = 'ignored',
                    parser_method = 'manual_ignore',
                    parser_reason = 'manual_ignore',
                    processed_at = ?
                WHERE id IN ({placeholders})
                  AND classification_status = 'review'
                """,
                (utc_now(), *safe_ids),
            )
            return int(cursor.rowcount)

    def create_show(
        self,
        *,
        title: str,
        normalized_title: str,
        aliases: Sequence[str] = (),
        status: str = "updating",
    ) -> dict[str, Any]:
        safe_title = title.strip()
        safe_normalized = normalized_title.strip()
        if not safe_title or not safe_normalized:
            raise ValueError("剧名和标准化剧名不能为空")
        if status not in SHOW_STATUSES:
            raise ValueError("短剧状态无效")
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO shows(title, normalized_title, aliases, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (safe_title, safe_normalized, _json_array(aliases), status, now, now),
            )
            row = connection.execute("SELECT * FROM shows WHERE normalized_title = ?", (safe_normalized,)).fetchone()
            if row is None:
                raise RuntimeError("保存短剧后无法读取记录")
            return _show_row(row)

    def get_show(self, show_id: int) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row) if row else None

    def get_show_by_normalized_title(self, normalized_title: str) -> dict[str, Any] | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM shows WHERE normalized_title = ?", (normalized_title,)
            ).fetchone()
            return _show_row(row) if row else None

    def get_show_by_title_or_alias(self, title: str) -> dict[str, Any] | None:
        normalized = normalize_title(title)
        if not normalized:
            return None
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM shows ORDER BY id").fetchall()
            for row in rows:
                show = _show_row(row)
                candidates = [show["title"], *show["aliases"]]
                if any(normalize_title(str(candidate)) == normalized for candidate in candidates):
                    return show
        return None

    def list_shows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM shows ORDER BY latest_update_at DESC, updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
            return [_show_row(row) for row in rows]

    def list_show_summaries(
        self,
        *,
        account_id: str | None = None,
        ignored: str = "normal",
        following: bool | None = None,
        include_empty: bool = False,
        q: str | None = None,
        sort: str = "recent",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return filterable library rows derived from persisted episodes and sources."""
        if ignored not in {"normal", "ignored", "all"}:
            raise ValueError("短剧忽略状态筛选无效")
        order_by = {
            "recent": "shows.latest_update_at DESC, shows.updated_at DESC",
            "title": "shows.title COLLATE NOCASE ASC, shows.id ASC",
            "episode_count": "episode_count DESC, shows.latest_update_at DESC",
            "latest_episode": "shows.latest_season DESC, shows.latest_episode DESC, shows.latest_update_at DESC",
        }.get(sort)
        if order_by is None:
            raise ValueError("短剧排序方式无效")
        safe_limit = max(1, min(int(limit), 500))
        conditions: list[str] = []
        params: list[Any] = []
        if ignored != "all":
            conditions.append("shows.is_ignored = ?")
            params.append(1 if ignored == "ignored" else 0)
        if following is not None:
            conditions.append("shows.is_following = ?")
            params.append(int(following))
        if not include_empty:
            conditions.append("EXISTS (SELECT 1 FROM episodes e0 WHERE e0.show_id = shows.id)")
        if account_id:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM episodes filtered_episodes
                    JOIN episode_sources filtered_sources
                      ON filtered_sources.episode_id = filtered_episodes.id
                    WHERE filtered_episodes.show_id = shows.id
                      AND filtered_sources.account_id = ?
                )
                """
            )
            params.append(account_id)
        search = str(q or "").strip()
        if search:
            conditions.append(
                "(shows.title LIKE ? COLLATE NOCASE OR shows.normalized_title LIKE ? "
                "COLLATE NOCASE OR shows.aliases LIKE ? COLLATE NOCASE)"
            )
            params.extend((f"%{search}%", f"%{normalize_title(search)}%", f"%{search}%"))
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    shows.*,
                    COUNT(DISTINCT episodes.id) AS episode_count,
                    COUNT(DISTINCT CASE WHEN episodes.season_number = shows.latest_season
                                              AND episodes.episode_number > 0 THEN episodes.id END)
                        AS regular_episode_count,
                    COUNT(DISTINCT CASE WHEN episodes.season_number = shows.latest_season
                                              AND episodes.episode_number = 0 THEN episodes.id END)
                        AS special_episode_count,
                    MIN(CASE WHEN episodes.season_number = shows.latest_season
                             THEN episodes.episode_number END) AS min_episode,
                    MAX(CASE WHEN episodes.season_number = shows.latest_season
                             THEN episodes.episode_number END) AS max_episode,
                    CASE
                        WHEN MAX(CASE WHEN episodes.season_number = shows.latest_season
                                           AND episodes.episode_number > 0
                                      THEN episodes.episode_number END) IS NULL THEN 0
                        ELSE MAX(CASE WHEN episodes.season_number = shows.latest_season
                                          AND episodes.episode_number > 0
                                      THEN episodes.episode_number END)
                             - COUNT(DISTINCT CASE WHEN episodes.season_number = shows.latest_season
                                                       AND episodes.episode_number > 0
                                                   THEN episodes.id END)
                    END AS missing_episode_count,
                    latest_source.account_id AS latest_account_id,
                    accounts.nickname AS latest_account_nickname,
                    latest_video.video_url AS latest_video_url,
                    latest_video.cover_url AS latest_cover_url,
                    COUNT(DISTINCT episode_sources.account_id) AS source_account_count
                FROM shows
                LEFT JOIN episodes ON episodes.show_id = shows.id
                LEFT JOIN episode_sources ON episode_sources.episode_id = episodes.id
                LEFT JOIN episode_sources AS latest_source
                    ON latest_source.id = (
                        SELECT sources_inner.id
                        FROM episode_sources AS sources_inner
                        JOIN episodes AS episodes_inner
                          ON episodes_inner.id = sources_inner.episode_id
                        WHERE episodes_inner.show_id = shows.id
                        ORDER BY COALESCE(sources_inner.published_at, sources_inner.created_at) DESC,
                                 sources_inner.id DESC
                        LIMIT 1
                    )
                LEFT JOIN accounts ON accounts.id = latest_source.account_id
                LEFT JOIN videos AS latest_video ON latest_video.id = latest_source.video_id
                {where_clause}
                GROUP BY shows.id
                ORDER BY {order_by}
                LIMIT ?
                """,
                (*params, safe_limit),
            ).fetchall()
            result = [_show_row(row) for row in rows]
            source_accounts = self._source_accounts_for_shows(
                connection, [int(show["id"]) for show in result]
            )
            for show in result:
                show["source_accounts"] = source_accounts.get(int(show["id"]), [])
            return result

    def _source_accounts_for_shows(
        self, connection: sqlite3.Connection, show_ids: Sequence[int]
    ) -> dict[int, list[dict[str, Any]]]:
        if not show_ids:
            return {}
        placeholders = ",".join("?" for _ in show_ids)
        rows = connection.execute(
            f"""
            SELECT episodes.show_id, accounts.id, accounts.nickname,
                   MAX(COALESCE(episode_sources.published_at, episode_sources.created_at))
                       AS latest_source_at
            FROM episodes
            JOIN episode_sources ON episode_sources.episode_id = episodes.id
            JOIN accounts ON accounts.id = episode_sources.account_id
            WHERE episodes.show_id IN ({placeholders})
            GROUP BY episodes.show_id, accounts.id, accounts.nickname
            ORDER BY latest_source_at DESC, accounts.nickname COLLATE NOCASE
            """,
            tuple(show_ids),
        ).fetchall()
        result: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(int(row["show_id"]), []).append(
                {"id": str(row["id"]), "nickname": str(row["nickname"])}
            )
        return result

    def get_show_detail(self, show_id: int) -> dict[str, Any] | None:
        with self._transaction() as connection:
            show_row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            if show_row is None:
                return None
            show = _show_row(show_row)
            episode_rows = connection.execute(
                "SELECT * FROM episodes WHERE show_id = ? "
                "ORDER BY season_number DESC, episode_number DESC",
                (show_id,),
            ).fetchall()
            episodes = [_episode_row(row) for row in episode_rows]
            sources = self._sources_for_episodes(
                connection, [int(episode["id"]) for episode in episodes]
            )
            for episode in episodes:
                episode["sources"] = sources.get(int(episode["id"]), [])
            source_accounts = self._source_accounts_for_shows(connection, [show_id])
        show["episodes"] = episodes
        latest_season = int(show.get("latest_season") or 1)
        latest_episode = int(show["latest_episode"] or 0)
        known_numbers = {
            int(episode["episode_number"])
            for episode in episodes
            if int(episode["season_number"]) == latest_season
        }
        show["missing_episode_numbers"] = [
            number for number in range(1, latest_episode + 1) if number not in known_numbers
        ]
        show["episode_count"] = len(episodes)
        show["regular_episode_count"] = sum(
            1 for episode in episodes if int(episode["episode_number"]) > 0
        )

        show["special_episode_count"] = sum(
            1 for episode in episodes if int(episode["episode_number"]) == 0
        )
        show["min_episode"] = min(known_numbers) if known_numbers else None
        show["max_episode"] = max(known_numbers) if known_numbers else None
        show["missing_episode_count"] = len(show["missing_episode_numbers"])
        seasons: dict[int, list[dict[str, Any]]] = {}
        for episode in episodes:
            seasons.setdefault(int(episode["season_number"]), []).append(episode)
        show["seasons"] = [
            {
                "season_number": season_number,
                "episodes": season_episodes,
                "missing_episode_numbers": [
                    number
                    for number in range(
                        1,
                        max(
                            (int(item["episode_number"]) for item in season_episodes),
                            default=0,
                        ) + 1,
                    )
                    if number not in {
                        int(item["episode_number"]) for item in season_episodes
                    }
                ],
            }
            for season_number, season_episodes in sorted(seasons.items(), reverse=True)
        ]
        show["source_accounts"] = source_accounts.get(show_id, [])
        show["source_account_count"] = len(show["source_accounts"])
        return show

    def _sources_for_episodes(
        self, connection: sqlite3.Connection, episode_ids: Sequence[int]
    ) -> dict[int, list[dict[str, Any]]]:
        if not episode_ids:
            return {}
        placeholders = ",".join("?" for _ in episode_ids)
        rows = connection.execute(
            f"""
            SELECT episode_sources.*, videos.aweme_id, videos.video_url, videos.cover_url,
                   videos.description, accounts.nickname AS account_nickname
            FROM episode_sources
            JOIN videos ON videos.id = episode_sources.video_id
            JOIN accounts ON accounts.id = episode_sources.account_id
            WHERE episode_sources.episode_id IN ({placeholders})
            ORDER BY episode_sources.episode_id,
                     COALESCE(episode_sources.published_at, episode_sources.created_at),
                     episode_sources.id
            """,
            tuple(episode_ids),
        ).fetchall()
        result: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(int(row["episode_id"]), []).append(_episode_source_row(row))
        return result

    def search_videos(self, *, page: int = 1, page_size: int = 50, account_id: str | None = None, show_id: int | None = None, classification_status: str | None = None, parser_method: str | None = None, content_type: str | None = None, q: str | None = None, date_from: str | None = None, date_to: str | None = None, needs_review: bool | None = None) -> dict[str, Any]:
        safe_page, safe_size = max(1, int(page)), max(1, min(int(page_size), 200))
        joins = " FROM videos JOIN accounts ON accounts.id=videos.account_id LEFT JOIN episode_sources es ON es.video_id=videos.id LEFT JOIN episodes e ON e.id=es.episode_id LEFT JOIN shows ON shows.id=e.show_id "
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("videos.account_id", account_id), ("shows.id", show_id), ("videos.classification_status", classification_status), ("videos.parser_method", parser_method), ("videos.content_type", content_type)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        if needs_review is not None:
            clauses.append("videos.needs_review=?"); params.append(int(needs_review))
        if q and q.strip():
            term = f"%{q.strip()}%"; clauses.append("(videos.display_title LIKE ? OR videos.description LIKE ? OR videos.parsed_show_title LIKE ? OR videos.show_title_candidate LIKE ? OR videos.aweme_id LIKE ?)"); params.extend([term] * 5)
        if date_from: clauses.append("COALESCE(videos.publish_time,videos.created_at)>=?"); params.append(date_from)
        if date_to: clauses.append("COALESCE(videos.publish_time,videos.created_at)<=?"); params.append(date_to)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._transaction() as connection:
            total = int(connection.execute("SELECT COUNT(DISTINCT videos.id)" + joins + where, params).fetchone()[0])
            rows = connection.execute("SELECT videos.*,accounts.nickname account_nickname,accounts.sec_uid account_sec_uid,shows.id show_id,shows.title show_title" + joins + where + " ORDER BY COALESCE(videos.publish_time,videos.created_at) DESC,videos.id DESC LIMIT ? OFFSET ?", (*params, safe_size, (safe_page - 1) * safe_size)).fetchall()
        return {"videos": [_video_row(row) | {"show_id": row["show_id"], "show_title": row["show_title"]} for row in rows], "total": total, "page": safe_page, "page_size": safe_size, "total_pages": (total + safe_size - 1) // safe_size}

    def list_show_candidates(self) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM shows WHERE is_ignored = 0 ORDER BY title COLLATE NOCASE"
            ).fetchall()
            return [_show_row(row) for row in rows]

    def update_show(
        self,
        show_id: int,
        *,
        title: str | None = None,
        aliases: Sequence[str] | None = None,
        status: str | None = None,
        expected_episode_count: int | None | object = _UNSET,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if title is not None:
            cleaned_title = title.strip()
            if not cleaned_title:
                raise ValueError("剧名不能为空")
            normalized_title = normalize_title(cleaned_title)
            if not normalized_title:
                raise ValueError("剧名标准化结果不能为空")
            changes["title"] = cleaned_title
            changes["normalized_title"] = normalized_title
        if aliases is not None:
            cleaned_aliases = _merge_show_aliases(title or "", aliases)
            changes["aliases"] = _json_array(cleaned_aliases)
        if status is not None:
            if status not in SHOW_STATUSES:
                raise ValueError("短剧状态无效")
            changes["status"] = status
        if expected_episode_count is not _UNSET:
            if expected_episode_count is not None and int(expected_episode_count) <= 0:
                raise ValueError("预计总集数必须是正整数或留空")
            changes["expected_episode_count"] = (
                None if expected_episode_count is None else int(expected_episode_count)
            )
        if not changes:
            existing = self.get_show(show_id)
            if existing is None:
                raise KeyError("短剧不存在")
            return existing
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self._transaction() as connection:
            existing = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            if existing is None:
                raise KeyError("短剧不存在")
            normalized_title = changes.get("normalized_title")
            if normalized_title is not None:
                conflict = connection.execute(
                    "SELECT id, title FROM shows WHERE normalized_title = ? AND id != ?",
                    (normalized_title, show_id),
                ).fetchone()
                if conflict is not None:
                    raise ValueError(
                        f"标准化剧名已被“{conflict['title']}”占用，请使用短剧合并"
                    )
            if aliases is not None:
                for alias in cleaned_aliases:
                    conflict = self._find_alias_conflict(connection, alias, show_id)
                    if conflict is not None:
                        raise ValueError(f"该别名已用于《{conflict['title']}》")
            cursor = connection.execute(
                f"UPDATE shows SET {assignments} WHERE id = ?", (*changes.values(), show_id)
            )
            if cursor.rowcount == 0:
                raise KeyError("短剧不存在")
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row)

    def add_show_alias(self, show_id: int, alias: str) -> dict[str, Any]:
        cleaned = alias.strip()
        if not cleaned or not normalize_title(cleaned):
            raise ValueError("别名不能为空")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
            if row is None:
                raise KeyError("短剧不存在")
            show = _show_row(row)
            if normalize_title(cleaned) == normalize_title(str(show["title"])):
                return show
            conflict = self._find_alias_conflict(connection, cleaned, show_id)
            if conflict is not None:
                raise ValueError(f"该别名已用于《{conflict['title']}》")
            aliases = _merge_show_aliases(str(show["title"]), show["aliases"], [cleaned])
            connection.execute("UPDATE shows SET aliases=?,updated_at=? WHERE id=?", (_json_array(aliases), utc_now(), show_id))
            return _show_row(connection.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone())

    def _find_alias_conflict(self, connection: sqlite3.Connection, alias: str, show_id: int) -> sqlite3.Row | None:
        normalized = normalize_title(alias)
        for row in connection.execute("SELECT id,title,normalized_title,aliases FROM shows WHERE id!=?", (show_id,)):
            if normalized in {normalize_title(str(row["title"])), str(row["normalized_title"]), *(normalize_title(item) for item in _json_list(row["aliases"]))}:
                return row
        return None

    def ignore_show(self, show_id: int, *, reason: str | None = None) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE shows
                SET is_ignored = 1, ignored_at = ?, ignore_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, _optional_text(reason), now, show_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("短剧不存在")
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row)

    def restore_show(self, show_id: int) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE shows
                SET is_ignored = 0, ignored_at = NULL, ignore_reason = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, show_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("短剧不存在")
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row)

    def set_show_following(self, show_id: int, *, following: bool) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE shows
                SET is_following = ?, followed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(following), now if following else None, now, show_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("短剧不存在")
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row)

    def merge_show(self, source_show_id: int, target_show_id: int) -> dict[str, Any]:
        """Merge a manually selected duplicate show into its canonical target.

        Episode sources and notification history are retained even when both
        shows already have the same episode number. The entire move uses one
        SQLite transaction so an error cannot leave a partially merged show.
        """
        if source_show_id == target_show_id:
            raise ValueError("源短剧和保留短剧不能相同")
        now = utc_now()
        with self._transaction() as connection:
            source_show = connection.execute(
                "SELECT * FROM shows WHERE id = ?", (source_show_id,)
            ).fetchone()
            target_show = connection.execute(
                "SELECT * FROM shows WHERE id = ?", (target_show_id,)
            ).fetchone()
            if source_show is None:
                raise KeyError("源短剧不存在")
            if target_show is None:
                raise KeyError("保留短剧不存在")

            aliases = _merge_show_aliases(
                str(target_show["title"]),
                _json_list(target_show["aliases"]),
                [str(source_show["title"])],
                _json_list(source_show["aliases"]),
            )
            connection.execute(
                "UPDATE shows SET aliases = ?, updated_at = ? WHERE id = ?",
                (_json_array(aliases), now, target_show_id),
            )

            target_episodes = {
                (int(row["season_number"]), int(row["episode_number"])): int(row["id"])
                for row in connection.execute(
                    "SELECT id, season_number, episode_number FROM episodes WHERE show_id = ?", (target_show_id,)
                ).fetchall()
            }
            source_episodes = connection.execute(
                "SELECT id, season_number, episode_number FROM episodes WHERE show_id = ?", (source_show_id,)
            ).fetchall()
            connection.execute(
                "UPDATE update_events SET show_id = ? WHERE show_id = ?",
                (target_show_id, source_show_id),
            )
            for source_episode in source_episodes:
                source_episode_id = int(source_episode["id"])
                season_number = int(source_episode["season_number"])
                episode_number = int(source_episode["episode_number"])
                episode_key = (season_number, episode_number)
                target_episode_id = target_episodes.get(episode_key)
                if target_episode_id is None:
                    connection.execute(
                        "UPDATE episodes SET show_id = ? WHERE id = ?",
                        (target_show_id, source_episode_id),
                    )
                    target_episodes[episode_key] = source_episode_id
                    continue

                connection.execute(
                    "UPDATE episode_sources SET episode_id = ? WHERE episode_id = ?",
                    (target_episode_id, source_episode_id),
                )
                self._merge_update_event(connection, source_episode_id, target_episode_id,
                                         target_show_id, season_number, episode_number)
                connection.execute(
                    """
                    UPDATE notifications
                    SET episode_id = ?, show_id = ?
                    WHERE episode_id = ?
                    """,
                    (target_episode_id, target_show_id, source_episode_id),
                )
                connection.execute("DELETE FROM episodes WHERE id = ?", (source_episode_id,))

            # Notifications for moved (rather than coalesced) episodes still
            # point at the source show, so update them before deleting it.
            connection.execute(
                "UPDATE notifications SET show_id = ? WHERE show_id = ?",
                (target_show_id, source_show_id),
            )
            connection.execute(
                """
                UPDATE videos
                SET parsed_show_title = ?
                WHERE id IN (
                    SELECT episode_sources.video_id
                    FROM episode_sources
                    JOIN episodes ON episodes.id = episode_sources.episode_id
                    WHERE episodes.show_id = ?
                )
                """,
                (str(target_show["title"]), target_show_id),
            )
            connection.execute("DELETE FROM shows WHERE id = ?", (source_show_id,))

            target_episode_rows = connection.execute(
                "SELECT id FROM episodes WHERE show_id = ?", (target_show_id,)
            ).fetchall()
            for episode_row in target_episode_rows:
                self._repair_episode_first_source(connection, int(episode_row["id"]))
            self._refresh_show_latest(connection, {target_show_id})
            self._record_correction(connection, "merge_show",
                                    {"show_id": source_show_id}, {"show_id": target_show_id})
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (target_show_id,)).fetchone()
            if row is None:
                raise RuntimeError("合并短剧后无法读取保留短剧")
            return _show_row(row)

    def _record_correction(self, connection: sqlite3.Connection, operation_type: str,
                           old_value: Mapping[str, Any], new_value: Mapping[str, Any]) -> None:
        connection.execute(
            "INSERT INTO manual_corrections(operation_type,old_value,new_value,created_at) VALUES (?,?,?,?)",
            (operation_type, json.dumps(dict(old_value), ensure_ascii=False),
             json.dumps(dict(new_value), ensure_ascii=False), utc_now()),
        )

    def _merge_update_event(self, connection: sqlite3.Connection, source_episode_id: int,
                            target_episode_id: int, show_id: int, season_number: int,
                            episode_number: int) -> None:
        source = connection.execute(
            "SELECT * FROM update_events WHERE episode_id=? AND event_type='new_episode'",
            (source_episode_id,),
        ).fetchone()
        if source is None:
            return
        target = connection.execute(
            "SELECT * FROM update_events WHERE episode_id=? AND event_type='new_episode'",
            (target_episode_id,),
        ).fetchone()
        if target is None:
            connection.execute(
                "UPDATE update_events SET episode_id=?,show_id=?,season_number=?,episode_number=? WHERE id=?",
                (target_episode_id, show_id, season_number, episode_number, source["id"]),
            )
            return
        occurred_at = min(str(source["occurred_at"]), str(target["occurred_at"]))
        read_at = None if source["read_at"] is None or target["read_at"] is None else max(str(source["read_at"]), str(target["read_at"]))
        connection.execute(
            "UPDATE update_events SET occurred_at=?,read_at=? WHERE id=?",
            (occurred_at, read_at, target["id"]),
        )
        connection.execute("DELETE FROM update_events WHERE id=?", (source["id"],))

    def move_episode(self, episode_id: int, *, target_show_id: int,
                     season_number: int, episode_number: int) -> dict[str, Any]:
        if season_number < 1 or episode_number < 0:
            raise ValueError("季数必须大于零，集数不能小于零")
        with self._transaction() as connection:
            episode = connection.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            if episode is None:
                raise KeyError("剧集不存在")
            if connection.execute("SELECT id FROM shows WHERE id=?", (target_show_id,)).fetchone() is None:
                raise KeyError("目标短剧不存在")
            old_show_id = int(episode["show_id"])
            old = {"episode_id": episode_id, "show_id": old_show_id,
                   "season_number": int(episode["season_number"]),
                   "episode_number": int(episode["episode_number"])}
            target = connection.execute(
                "SELECT * FROM episodes WHERE show_id=? AND season_number=? AND episode_number=? AND id!=?",
                (target_show_id, season_number, episode_number, episode_id),
            ).fetchone()
            final_id = episode_id
            if target is None:
                connection.execute(
                    "UPDATE episodes SET show_id=?,season_number=?,episode_number=? WHERE id=?",
                    (target_show_id, season_number, episode_number, episode_id),
                )
                connection.execute("UPDATE notifications SET show_id=? WHERE episode_id=?",
                                   (target_show_id, episode_id))
                connection.execute(
                    "UPDATE update_events SET show_id=?,season_number=?,episode_number=? WHERE episode_id=?",
                    (target_show_id, season_number, episode_number, episode_id),
                )
            else:
                final_id = int(target["id"])
                connection.execute("UPDATE episode_sources SET episode_id=? WHERE episode_id=?",
                                   (final_id, episode_id))
                connection.execute("UPDATE notifications SET episode_id=?,show_id=? WHERE episode_id=?",
                                   (final_id, target_show_id, episode_id))
                self._merge_update_event(connection, episode_id, final_id, target_show_id,
                                         season_number, episode_number)
                connection.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
            self._repair_episode_first_source(connection, final_id)
            self._refresh_show_latest(connection, {old_show_id, target_show_id})
            new = {"episode_id": final_id, "show_id": target_show_id,
                   "season_number": season_number, "episode_number": episode_number}
            self._record_correction(connection, "move_episode", old, new)
        return {"episode_id": final_id, "merged": final_id != episode_id,
                "show": self.get_show_detail(target_show_id)}

    def move_episode_source(self, source_id: int, *, target_show_id: int,
                            season_number: int, episode_number: int) -> dict[str, Any]:
        if season_number < 1 or episode_number < 0:
            raise ValueError("季数必须大于零，集数不能小于零")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT episode_sources.*,episodes.show_id,episodes.season_number AS old_season,episodes.episode_number AS old_episode FROM episode_sources JOIN episodes ON episodes.id=episode_sources.episode_id WHERE episode_sources.id=?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise KeyError("剧集来源不存在")
            if connection.execute("SELECT id FROM shows WHERE id=?", (target_show_id,)).fetchone() is None:
                raise KeyError("目标短剧不存在")
            old_episode_id, old_show_id = int(source["episode_id"]), int(source["show_id"])
            target = connection.execute(
                "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                (target_show_id, season_number, episode_number),
            ).fetchone()
            if target is None:
                cursor = connection.execute(
                    "INSERT INTO episodes(show_id,season_number,episode_number,first_video_id,first_account_id,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                    (target_show_id, season_number, episode_number, source["video_id"],
                     source["account_id"], source["published_at"], utc_now()),
                )
                target_episode_id = int(cursor.lastrowid)
            else:
                target_episode_id = int(target["id"])
            connection.execute("UPDATE episode_sources SET episode_id=? WHERE id=?",
                               (target_episode_id, source_id))
            remaining = int(connection.execute(
                "SELECT COUNT(*) FROM episode_sources WHERE episode_id=?", (old_episode_id,)
            ).fetchone()[0])
            if remaining:
                self._repair_episode_first_source(connection, old_episode_id)
            else:
                connection.execute("DELETE FROM episodes WHERE id=?", (old_episode_id,))
            self._repair_episode_first_source(connection, target_episode_id)
            self._refresh_show_latest(connection, {old_show_id, target_show_id})
            self._record_correction(connection, "move_episode_source",
                {"source_id": source_id, "show_id": old_show_id, "season_number": source["old_season"], "episode_number": source["old_episode"]},
                {"source_id": source_id, "show_id": target_show_id, "season_number": season_number, "episode_number": episode_number})
        return {"source_id": source_id, "episode_id": target_episode_id,
                "old_episode_removed": not bool(remaining),
                "show": self.get_show_detail(target_show_id)}

    def batch_update_episode_season(self, episode_ids: Sequence[int], season_number: int) -> int:
        if not episode_ids or season_number < 1:
            raise ValueError("请选择剧集并提供有效季数")
        moved = 0
        for episode_id in dict.fromkeys(int(value) for value in episode_ids):
            with self._transaction() as connection:
                row = connection.execute("SELECT show_id,episode_number FROM episodes WHERE id=?", (episode_id,)).fetchone()
            if row is None:
                raise KeyError("剧集不存在")
            self.move_episode(episode_id, target_show_id=int(row["show_id"]),
                              season_number=season_number, episode_number=int(row["episode_number"]))
            moved += 1
        return moved

    def ignore_videos(self, video_ids: Sequence[int]) -> int:
        ids = list(dict.fromkeys(int(value) for value in video_ids))
        if not ids:
            return 0
        with self._transaction() as connection:
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT es.id,es.episode_id,e.show_id FROM episode_sources es JOIN episodes e ON e.id=es.episode_id WHERE es.video_id IN ({placeholders})", ids
            ).fetchall()
            affected_episodes = {int(row["episode_id"]) for row in rows}
            affected_shows = {int(row["show_id"]) for row in rows}
            connection.execute(f"DELETE FROM episode_sources WHERE video_id IN ({placeholders})", ids)
            for episode_id in affected_episodes:
                if int(connection.execute("SELECT COUNT(*) FROM episode_sources WHERE episode_id=?", (episode_id,)).fetchone()[0]):
                    self._repair_episode_first_source(connection, episode_id)
                else:
                    connection.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
            self._mark_removed_videos(connection, ids, parser_reason="manual_batch_ignore")
            self._refresh_show_latest(connection, affected_shows)
            self._record_correction(connection, "batch_ignore_videos", {"video_ids": ids}, {"classification_status": "ignored"})
            return int(connection.execute(f"SELECT COUNT(*) FROM videos WHERE id IN ({placeholders})", ids).fetchone()[0])

    def list_manual_corrections(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM manual_corrections ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [{**dict(row), "old_value": json.loads(row["old_value"]), "new_value": json.loads(row["new_value"])} for row in rows]

    def data_quality_report(self, *, stale_days: int = 30, limit: int = 50) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 200))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, stale_days))).isoformat(timespec="seconds")
        definitions = {
            "review": ("SELECT videos.id,accounts.nickname,COALESCE(videos.display_title,videos.description) title FROM videos JOIN accounts ON accounts.id=videos.account_id WHERE videos.classification_status='review' ORDER BY videos.created_at DESC", ()),
            "missing_episodes": ("""SELECT shows.id,shows.title,shows.latest_season,shows.latest_episode,
                (SELECT MAX(e.episode_number)-MIN(e.episode_number)+1-COUNT(*) FROM episodes e WHERE e.show_id=shows.id AND e.season_number=shows.latest_season AND e.episode_number>0) issue_count
                FROM shows WHERE is_ignored=0 AND COALESCE((SELECT MAX(e.episode_number)-MIN(e.episode_number)+1-COUNT(*) FROM episodes e WHERE e.show_id=shows.id AND e.season_number=shows.latest_season AND e.episode_number>0),0)>0 ORDER BY issue_count DESC""", ()),
            "suspicious_jumps": ("""SELECT shows.id,shows.title,MAX(episodes.episode_number)-MIN(episodes.episode_number) jump_size FROM shows JOIN episodes ON episodes.show_id=shows.id WHERE episodes.episode_number>0 GROUP BY shows.id HAVING jump_size>20 AND COUNT(*)<4 ORDER BY jump_size DESC""", ()),
            "expected_count_conflicts": ("""SELECT shows.id,shows.title,shows.expected_episode_count,shows.latest_episode,COUNT(episodes.id) collected_count FROM shows LEFT JOIN episodes ON episodes.show_id=shows.id WHERE shows.expected_episode_count IS NOT NULL GROUP BY shows.id HAVING shows.expected_episode_count<COUNT(episodes.id) OR shows.expected_episode_count<COALESCE(shows.latest_episode,0) ORDER BY shows.id""", ()),
            "source_less_episodes": ("""SELECT episodes.id,shows.id show_id,shows.title,episodes.season_number,episodes.episode_number FROM episodes JOIN shows ON shows.id=episodes.show_id LEFT JOIN episode_sources ON episode_sources.episode_id=episodes.id WHERE episode_sources.id IS NULL ORDER BY episodes.id""", ()),
            "low_confidence": ("""SELECT videos.id,accounts.nickname,COALESCE(videos.display_title,videos.description) title,videos.parser_confidence FROM videos JOIN accounts ON accounts.id=videos.account_id WHERE videos.classification_status!='ignored' AND videos.parser_confidence<0.90 ORDER BY videos.parser_confidence ASC""", ()),
            "ocr_only": ("""SELECT videos.id,accounts.nickname,COALESCE(videos.display_title,videos.description) title,videos.ocr_confidence FROM videos JOIN accounts ON accounts.id=videos.account_id WHERE videos.ocr_text IS NOT NULL AND videos.ocr_text!='' AND videos.parser_method LIKE 'ocr:%' ORDER BY videos.created_at DESC""", ()),
            "stale_shows": ("""SELECT id,title,latest_update_at FROM shows WHERE is_ignored=0 AND status='updating' AND (latest_update_at IS NULL OR latest_update_at<?) ORDER BY latest_update_at""", (cutoff,)),
        }
        result: dict[str, Any] = {"stale_days": max(1, stale_days), "categories": {}}
        with self._transaction() as connection:
            for name, (query, params) in definitions.items():
                count = int(
                    connection.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
                )
                rows = connection.execute(f"{query} LIMIT ?", (*params, safe_limit)).fetchall()
                result["categories"][name] = {
                    "count": count,
                    "items": [dict(row) for row in rows],
                }
        result["total_issues"] = sum(item["count"] for item in result["categories"].values())
        return result

    def repair_episode_and_show_consistency(self) -> dict[str, int]:
        """Rebuild canonical episode sources and cached show metadata safely."""
        with self._transaction() as connection:
            episode_rows = connection.execute("SELECT id FROM episodes ORDER BY id").fetchall()
            repaired_episodes = 0
            for episode_row in episode_rows:
                if self._repair_episode_first_source(connection, int(episode_row["id"])):
                    repaired_episodes += 1
            show_ids = {
                int(row["id"])
                for row in connection.execute("SELECT id FROM shows").fetchall()
            }
            repaired_shows = self._refresh_show_latest(connection, show_ids)
            return {
                "episodes_checked": len(episode_rows),
                "episodes_repaired": repaired_episodes,
                "shows_checked": len(show_ids),
                "shows_repaired": repaired_shows,
            }

    def record_episode_source(
        self,
        *,
        show_id: int,
        episode_number: int,
        video_id: int,
        account_id: str,
        published_at: str | None,
        season_number: int = 1,
        create_update_event: bool = False,
    ) -> EpisodeWriteResult:
        if episode_number < 0:
            raise ValueError("集数不能小于 0")
        if season_number < 1:
            raise ValueError("季数不能小于 1")
        now = utc_now()
        with self._transaction() as connection:
            show = connection.execute(
                "SELECT is_ignored FROM shows WHERE id = ?", (show_id,)
            ).fetchone()
            if show is None:
                raise KeyError("短剧不存在")
            if bool(show["is_ignored"]):
                raise ValueError("短剧已永久忽略")
            existing_episode = connection.execute(
                "SELECT * FROM episodes WHERE show_id = ? AND season_number = ? AND episode_number = ?",
                (show_id, season_number, episode_number),
            ).fetchone()
            is_new_episode = existing_episode is None
            if existing_episode is None:
                cursor = connection.execute(
                    """
                    INSERT INTO episodes(
                        show_id, season_number, episode_number, first_video_id,
                        first_account_id, published_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (show_id, season_number, episode_number, video_id, account_id, _optional_text(published_at), now),
                )
                episode_id = int(cursor.lastrowid)
            else:
                episode_id = int(existing_episode["id"])

            source_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO episode_sources(
                    episode_id, video_id, account_id, published_at, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (episode_id, video_id, account_id, _optional_text(published_at), now),
            )
            source_row = connection.execute(
                "SELECT * FROM episode_sources WHERE video_id = ?", (video_id,)
            ).fetchone()
            if source_row is None:
                raise RuntimeError("保存剧集来源后无法读取记录")
            if is_new_episode and create_update_event:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO update_events(
                        show_id, episode_id, season_number, episode_number,
                        account_id, video_id, event_type, occurred_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'new_episode', ?, ?)
                    """,
                    (
                        show_id,
                        episode_id,
                        season_number,
                        episode_number,
                        account_id,
                        video_id,
                        _optional_text(published_at) or now,
                        now,
                    ),
                )
            self._repair_episode_first_source(connection, episode_id)
            self._refresh_show_latest(connection, {show_id})
            episode_row = connection.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if episode_row is None:
                raise RuntimeError("保存剧集后无法读取记录")
            return EpisodeWriteResult(
                episode=_episode_row(episode_row),
                source=_episode_source_row(source_row),
                is_new_episode=is_new_episode,
                is_new_source=source_cursor.rowcount == 1,
            )

    def list_update_events(
        self,
        *,
        following_only: bool = False,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 200))
        conditions: list[str] = []
        if following_only:
            conditions.append("shows.is_following = 1")
        if unread_only:
            conditions.append("update_events.read_at IS NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._transaction() as connection:
            total = int(connection.execute(
                f"SELECT COUNT(*) FROM update_events JOIN shows ON shows.id = update_events.show_id {where}"
            ).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT update_events.*, shows.title AS show_title,
                       shows.is_following, accounts.nickname AS account_nickname,
                       videos.video_url, videos.cover_url
                FROM update_events
                JOIN shows ON shows.id = update_events.show_id
                JOIN accounts ON accounts.id = update_events.account_id
                JOIN videos ON videos.id = update_events.video_id
                {where}
                ORDER BY update_events.occurred_at DESC, update_events.id DESC
                LIMIT ? OFFSET ?
                """,
                (safe_page_size, (safe_page - 1) * safe_page_size),
            ).fetchall()
        return {
            "events": [_update_event_row(row) for row in rows],
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "has_more": safe_page * safe_page_size < total,
        }

    def unread_update_count(self, *, following_only: bool = False) -> int:
        query = """SELECT COUNT(*) FROM update_events
                   JOIN shows ON shows.id = update_events.show_id
                   WHERE update_events.read_at IS NULL"""
        if following_only:
            query += " AND shows.is_following = 1"
        with self._transaction() as connection:
            return int(connection.execute(query).fetchone()[0])

    def mark_update_read(self, event_id: int) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE update_events SET read_at = COALESCE(read_at, ?) WHERE id = ?",
                (utc_now(), event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("更新事件不存在")
            row = connection.execute(
                "SELECT * FROM update_events WHERE id = ?", (event_id,)
            ).fetchone()
            return _update_event_row(row)

    def mark_updates_read(self, *, show_id: int | None = None) -> int:
        query = "UPDATE update_events SET read_at = ? WHERE read_at IS NULL"
        params: tuple[Any, ...] = (utc_now(),)
        if show_id is not None:
            query += " AND show_id = ?"
            params = (*params, show_id)
        with self._transaction() as connection:
            return int(connection.execute(query, params).rowcount)

    def get_show_episodes(self, show_id: int) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM episodes WHERE show_id = ? "
                "ORDER BY season_number DESC, episode_number DESC", (show_id,)
            ).fetchall()
            return [_episode_row(row) for row in rows]

    def get_episode_sources(self, episode_id: int) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT episode_sources.*, videos.aweme_id, videos.video_url, videos.cover_url,
                       videos.description, accounts.nickname AS account_nickname
                FROM episode_sources
                JOIN videos ON videos.id = episode_sources.video_id
                JOIN accounts ON accounts.id = episode_sources.account_id
                WHERE episode_sources.episode_id = ?
                ORDER BY COALESCE(episode_sources.published_at, episode_sources.created_at) ASC
                """,
                (episode_id,),
            ).fetchall()
            return [_episode_source_row(row) for row in rows]

    def remove_episode(self, show_id: int, episode_id: int) -> dict[str, Any]:
        with self._transaction() as connection:
            episode = connection.execute(
                "SELECT * FROM episodes WHERE id = ? AND show_id = ?",
                (episode_id, show_id),
            ).fetchone()
            if episode is None:
                raise KeyError("剧集不存在")
            source_rows = connection.execute(
                "SELECT video_id FROM episode_sources WHERE episode_id = ?",
                (episode_id,),
            ).fetchall()
            video_ids = [int(row["video_id"]) for row in source_rows]
            connection.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
            self._mark_removed_videos(
                connection,
                video_ids,
                parser_reason="manual_remove_episode",
            )
            self._refresh_show_latest(connection, {show_id})
        return {
            "removed_episode_id": episode_id,
            "removed_source_count": len(video_ids),
            "show": self.get_show_detail(show_id),
        }

    def remove_episode_source(
        self, show_id: int, episode_id: int, source_id: int
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            source = connection.execute(
                """
                SELECT episode_sources.video_id
                FROM episode_sources
                JOIN episodes ON episodes.id = episode_sources.episode_id
                WHERE episode_sources.id = ? AND episodes.id = ? AND episodes.show_id = ?
                """,
                (source_id, episode_id, show_id),
            ).fetchone()
            if source is None:
                raise KeyError("剧集来源不存在")
            video_id = int(source["video_id"])
            connection.execute("DELETE FROM episode_sources WHERE id = ?", (source_id,))
            self._mark_removed_videos(
                connection,
                [video_id],
                parser_reason="manual_remove_source",
            )
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM episode_sources WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            episode_removed = int(remaining["count"]) == 0
            if episode_removed:
                connection.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
            else:
                self._repair_episode_first_source(connection, episode_id)
            self._refresh_show_latest(connection, {show_id})
        return {
            "removed_source_id": source_id,
            "episode_removed": episode_removed,
            "show": self.get_show_detail(show_id),
        }

    def _mark_removed_videos(
        self,
        connection: sqlite3.Connection,
        video_ids: Sequence[int],
        *,
        parser_reason: str,
    ) -> None:
        if not video_ids:
            return
        placeholders = ",".join("?" for _ in video_ids)
        connection.execute(
            f"""
            UPDATE videos
            SET is_processed = 1, needs_review = 0, classification_status = 'ignored',
                parser_confidence = 1.0, parsed_show_title = NULL,
                parsed_episode_number = NULL, parser_method = 'manual_remove',
                parser_reason = ?, processed_at = ?
            WHERE id IN ({placeholders})
            """,
            (parser_reason, utc_now(), *video_ids),
        )

    def record_notification(
        self,
        *,
        show_id: int,
        episode_id: int,
        channel: str,
        success: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        safe_channel = channel.strip()
        if not safe_channel:
            raise ValueError("通知渠道不能为空")
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO notifications(show_id, episode_id, channel, success, error, sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (show_id, episode_id, safe_channel, int(success), _optional_text(error), utc_now()),
            )
            row = connection.execute("SELECT * FROM notifications WHERE id = ?", (cursor.lastrowid,)).fetchone()
            if row is None:
                raise RuntimeError("保存通知后无法读取记录")
            return _notification_row(row)

    def list_notifications(self, *, episode_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM notifications"
        params: tuple[Any, ...] = ()
        if episode_id is not None:
            query += " WHERE episode_id = ?"
            params = (episode_id,)
        query += " ORDER BY sent_at DESC, id DESC"
        with self._transaction() as connection:
            return [_notification_row(row) for row in connection.execute(query, params).fetchall()]

    def mark_account_sync_success(self, account_id: str, *, next_check_at: str) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts SET
                    last_checked_at = ?, last_success_at = ?, next_check_at = ?, last_error = NULL,
                    consecutive_failures = 0, initial_sync_completed = 1, updated_at = ?
                WHERE id = ?
                """,
                (now, now, next_check_at, now, account_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def complete_initial_sync(self, account_id: str) -> dict[str, Any]:
        """Mark a successfully fetched account as having an historical baseline."""
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE accounts SET initial_sync_completed = 1, updated_at = ? WHERE id = ?",
                (utc_now(), account_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def mark_account_sync_failure(
        self,
        account_id: str,
        *,
        error: str,
        next_check_at: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts SET
                    last_checked_at = ?, next_check_at = ?, last_error = ?,
                    consecutive_failures = consecutive_failures + 1, updated_at = ?
                WHERE id = ?
                """,
                (now, next_check_at, error[:2000], now, account_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("账号不存在")
            return self._require_account(connection, account_id)

    def due_accounts(self, *, now: str, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM accounts
                WHERE enabled = 1 AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY COALESCE(next_check_at, created_at), created_at
                LIMIT ?
                """,
                (now, safe_limit),
            ).fetchall()
            return [_account_row(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._transaction() as connection:
            return {
                "accounts": _count(connection, "accounts"),
                "enabled_accounts": _count(connection, "accounts WHERE enabled = 1"),
                "shows": _count(connection, "shows"),
                "videos": _count(connection, "videos"),
                "pending_review": _count(connection, "videos WHERE classification_status = 'review'"),
            }

    def system_status(self) -> dict[str, Any]:
        with self._transaction() as connection:
            counts = {
                "accounts": _count(connection, "accounts"),
                "enabled_accounts": _count(connection, "accounts WHERE enabled = 1"),
                "shows": _count(connection, "shows"),
                "videos": _count(connection, "videos"),
                "pending_review": _count(connection, "videos WHERE classification_status = 'review'"),
            }
            last_checked = connection.execute("SELECT MAX(last_checked_at) FROM accounts").fetchone()[0]
            errors = connection.execute(
                """
                SELECT *
                FROM accounts
                WHERE last_error IS NOT NULL AND last_error != ''
                ORDER BY last_checked_at DESC
                LIMIT 10
                """
            ).fetchall()
            since = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 86400, timezone.utc).isoformat(timespec="seconds")
            scan = connection.execute("""SELECT COUNT(*) runs, COALESCE(SUM(success),0) successes,
                COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) failures,
                COALESCE(SUM(new_videos),0) new_videos, COALESCE(SUM(new_episodes),0) new_episodes,
                COALESCE(SUM(review_videos),0) review_videos FROM scan_runs WHERE started_at >= ?""", (since,)).fetchone()
            return {
                **counts,
                "last_check_at": last_checked,
                "recent_errors": [_account_row(row) for row in errors],
                "scan_runs_24h": dict(scan),
            }

    def _require_account(self, connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError("账号不存在")
        return _account_row(row)


def _count(connection: sqlite3.Connection, table_or_clause: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_or_clause}").fetchone()[0])


def _schema_version(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(str(row["name"]) == column for row in connection.execute(f"PRAGMA table_info({table})"))


def _episodes_allow_zero(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'episodes'"
    ).fetchone()
    sql = str(row["sql"] or "").replace(" ", "") if row else ""
    return "CHECK(episode_number>=0)" in sql


def _migrate_episode_seasons(connection: sqlite3.Connection) -> None:
    """Rebuild episodes in place while preserving ids used by child tables."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    connection.execute("ALTER TABLE episodes RENAME TO episodes_before_v9")
    connection.execute(
        """
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
            season_number INTEGER NOT NULL DEFAULT 1,
            episode_number INTEGER NOT NULL,
            first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
            first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            published_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(show_id, season_number, episode_number),
            CHECK (season_number >= 1),
            CHECK (episode_number >= 0)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO episodes(
            id, show_id, season_number, episode_number, first_video_id,
            first_account_id, published_at, created_at
        )
        SELECT id, show_id, 1, episode_number, first_video_id,
               first_account_id, published_at, created_at
        FROM episodes_before_v9
        """
    )
    connection.execute("DROP TABLE episodes_before_v9")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodes_show_number "
        "ON episodes(show_id, season_number DESC, episode_number DESC)"
    )
    connection.execute("PRAGMA legacy_alter_table = OFF")
    connection.execute("PRAGMA foreign_keys = ON")


def _migrate_episodes_allow_zero(connection: sqlite3.Connection) -> None:
    """Rebuild the table because SQLite cannot alter an existing CHECK constraint."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    connection.execute("ALTER TABLE episodes RENAME TO episodes_before_v6")
    connection.execute(
        """
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
            season_number INTEGER NOT NULL DEFAULT 1,
            episode_number INTEGER NOT NULL,
            first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
            first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            published_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(show_id, season_number, episode_number),
            CHECK (season_number >= 1),
            CHECK (episode_number >= 0)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO episodes(
            id, show_id, season_number, episode_number, first_video_id, first_account_id, published_at, created_at
        )
        SELECT id, show_id, season_number, episode_number, first_video_id, first_account_id, published_at, created_at
        FROM episodes_before_v6
        """
    )
    connection.execute("DROP TABLE episodes_before_v6")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodes_show_number "
        "ON episodes(show_id, season_number DESC, episode_number DESC)"
    )
    connection.execute("PRAGMA legacy_alter_table = OFF")


def _safe_account_nickname(value: Any, sec_uid: str) -> str:
    text = str(value or "").strip()
    return _fallback_account_nickname(sec_uid) if _is_placeholder_nickname(text) else text


def _is_placeholder_nickname(value: Any) -> bool:
    return str(value or "").strip().casefold() in _PLACEHOLDER_NICKNAMES


def _fallback_account_nickname(sec_uid: str) -> str:
    return f"作者 {str(sec_uid or '').strip()[:12]}"


def _validate_video_classification(value: str) -> None:
    if value not in VIDEO_CLASSIFICATIONS:
        raise ValueError("视频分类状态无效")


def _validate_video_content_type(value: str) -> None:
    if value not in VIDEO_CONTENT_TYPES:
        raise ValueError("视频内容类型无效")


def _classification_from_legacy_fields(
    *,
    needs_review: bool,
    parsed_show_title: str | None,
    parsed_episode_number: int | None,
) -> str:
    if needs_review:
        return "review"
    if parsed_show_title and parsed_episode_number is not None:
        return "matched"
    return "ignored"


def _video_ids(values: Sequence[int]) -> tuple[int, ...]:
    result: list[int] = []
    for value in values:
        try:
            video_id = int(value)
        except (TypeError, ValueError):
            continue
        if video_id > 0 and video_id not in result:
            result.append(video_id)
    return tuple(result)


def _legacy_download_records(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        aweme_id = str(item.get("aweme_id") or "").strip()
        if aweme_id:
            result[aweme_id] = item
    return result


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _merge_show_aliases(canonical_title: str, *groups: Sequence[str]) -> list[str]:
    """Retain human-recognizable prior titles without duplicating the canonical one."""
    canonical_normalized = normalize_title(canonical_title)
    seen = {canonical_normalized} if canonical_normalized else set()
    aliases: list[str] = []
    for group in groups:
        for raw_value in group:
            value = str(raw_value or "").strip()
            normalized = normalize_title(value)
            if not value or not normalized or normalized in seen:
                continue
            aliases.append(value)
            seen.add(normalized)
    return aliases


def _episode_source_published_at(source: sqlite3.Row) -> str | None:
    return _optional_text(source["source_published_at"]) or _optional_text(
        source["video_published_at"]
    )


def _episode_source_order_key(source: sqlite3.Row) -> tuple[int, tuple[int, str], tuple[int, str], int]:
    published_at = _episode_source_published_at(source)
    return (
        0 if published_at is not None else 1,
        _timestamp_order_key(published_at),
        _timestamp_order_key(source["created_at"]),
        int(source["id"]),
    )


def _timestamp_order_key(value: Any) -> tuple[int, str]:
    """Compare ISO timestamps and legacy slash-delimited dates consistently."""
    text = _optional_text(value)
    if text is None:
        return (1, "")
    normalized = text.replace("/", "-")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return (1, normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (0, parsed.astimezone(timezone.utc).isoformat(timespec="microseconds"))


def _json_array(values: Sequence[str]) -> str:
    result: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if value and value not in result:
            result.append(value)
    return json.dumps(result, ensure_ascii=False)


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_text_sources(values: Mapping[str, Any] | None) -> str:
    result: dict[str, str] = {}
    if isinstance(values, Mapping):
        for raw_field, raw_text in values.items():
            field = str(raw_field or "").strip()
            text = str(raw_text or "").strip()
            if field and text:
                result[field] = text
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def _json_mapping(values: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(values), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("解析证据必须是 JSON 对象") from exc


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_value(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _json_cursor_history(values: Sequence[int] | None) -> str:
    result: list[int] = []
    for value in values or ():
        cursor = _non_negative_int(value)
        if cursor not in result:
            result.append(cursor)
    return json.dumps(result or [0], separators=(",", ":"))


def _cursor_history(value: Any, current_cursor: int) -> list[int]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        parsed = []
    result = []
    if isinstance(parsed, list):
        for item in parsed:
            cursor = _non_negative_int(item)
            if cursor not in result:
                result.append(cursor)
    if current_cursor not in result:
        result.append(current_cursor)
    return result


def _prefer_non_empty(existing: Any, incoming: Any) -> str | None:
    current = _optional_text(existing)
    candidate = _optional_text(incoming)
    if not current:
        return candidate
    if not candidate:
        return current
    return _prefer_richer_text(current, candidate)


def _prefer_richer_text(existing: Any, incoming: Any) -> str | None:
    current = _optional_text(existing)
    candidate = _optional_text(incoming)
    if not candidate or candidate == current:
        return current
    if not current:
        return candidate
    current_compact = " ".join(current.split())
    candidate_compact = " ".join(candidate.split())
    if current_compact and current_compact in candidate_compact:
        return candidate
    if len(candidate_compact) >= max(len(current_compact) + 16, int(len(current_compact) * 1.5)):
        return candidate
    return current


def _merge_text_sources(existing: Any, incoming: Mapping[str, Any] | None) -> dict[str, str]:
    current = _json_object(existing)
    candidate = incoming if isinstance(incoming, Mapping) else {}
    merged: dict[str, str] = {}
    for raw_field in set(current) | set(candidate):
        field = str(raw_field or "").strip()
        if not field:
            continue
        value = _prefer_richer_text(current.get(raw_field), candidate.get(raw_field))
        if value:
            merged[field] = value
    return merged


def _merge_richer_json(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(existing) if isinstance(existing, Mapping) else {}
    candidate = dict(incoming) if isinstance(incoming, Mapping) else {}
    merged: dict[str, Any] = {}
    for key in set(current) | set(candidate):
        if key not in candidate:
            merged[key] = current[key]
        elif key not in current:
            merged[key] = candidate[key]
        else:
            merged[key] = _merge_json_value(current[key], candidate[key])
    return merged


def _merge_json_value(existing: Any, incoming: Any) -> Any:
    if isinstance(existing, Mapping) and isinstance(incoming, Mapping):
        return _merge_richer_json(existing, incoming)
    if not _json_value_present(incoming):
        return existing
    if not _json_value_present(existing):
        return incoming
    if isinstance(existing, str) and isinstance(incoming, str):
        return _prefer_richer_text(existing, incoming)
    if _json_value_richness(incoming) > _json_value_richness(existing):
        return incoming
    return existing


def _json_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set)):
        return bool(value)
    return True


def _json_value_richness(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(1 + _json_value_richness(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return sum(_json_value_richness(item) for item in value)
    if isinstance(value, str):
        return len(value.strip())
    return 1 if value is not None else 0


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _account_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["enabled"] = bool(result["enabled"])
    result["initial_sync_completed"] = bool(result["initial_sync_completed"])
    result["history_has_more"] = bool(result.get("history_has_more", 1))
    result["history_next_cursor"] = _non_negative_int(result.get("history_next_cursor"))
    result["history_processed_pages"] = _non_negative_int(result.get("history_processed_pages"))
    result["history_scanned_items"] = _non_negative_int(result.get("history_scanned_items"))
    result["history_new_videos"] = _non_negative_int(result.get("history_new_videos"))
    result["history_sync_status"] = str(result.get("history_sync_status") or "idle")
    result["history_cursor_history"] = _cursor_history(
        result.get("history_cursor_history"), result["history_next_cursor"]
    )
    result["history_sync"] = {
        "status": result["history_sync_status"],
        "next_cursor": result["history_next_cursor"],
        "has_more": result["history_has_more"],
        "processed_pages": result["history_processed_pages"],
        "scanned_items": result["history_scanned_items"],
        "new_videos": result["history_new_videos"],
        "started_at": result.get("history_started_at"),
        "updated_at": result.get("history_updated_at"),
        "completed_at": result.get("history_completed_at"),
        "last_error": result.get("history_last_error"),
        "cursor_history": result["history_cursor_history"],
    }
    return result


def _video_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["hashtags"] = _json_list(result.get("hashtags"))
    result["text_sources"] = _json_object(result.get("text_sources"))
    result["parser_evidence"] = _json_object(result.get("parser_evidence"))
    result["llm_raw_result"] = _json_value(result.get("llm_raw_result"))
    result["is_processed"] = bool(result["is_processed"])
    result["needs_review"] = bool(result["needs_review"])
    return result


def _show_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["aliases"] = _json_list(result.get("aliases"))
    result["is_ignored"] = bool(result.get("is_ignored", 0))
    result["is_following"] = bool(result.get("is_following", 0))
    if result.get("expected_episode_count") is not None:
        result["expected_episode_count"] = int(result["expected_episode_count"])
    return result


def _episode_row(row: sqlite3.Row) -> dict[str, Any]:
    return _row_to_dict(row)


def _episode_source_row(row: sqlite3.Row) -> dict[str, Any]:
    return _row_to_dict(row)


def _notification_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["success"] = bool(result["success"])
    return result


def _update_event_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    if "is_following" in result:
        result["is_following"] = bool(result["is_following"])
    return result
