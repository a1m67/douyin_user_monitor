"""SQLite persistence and legacy JSON migration for short-drama data."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
SHOW_STATUSES = frozenset({"updating", "completed", "paused"})


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
        with self._transaction() as connection:
            self._create_schema(connection)
            self._set_meta(connection, "schema_version", str(SCHEMA_VERSION))
            if self._get_meta(connection, "legacy_state_imported") is None:
                self._import_legacy_state(connection)
                self._set_meta(connection, "legacy_state_imported", utc_now())

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
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
                is_processed INTEGER NOT NULL DEFAULT 0,
                needs_review INTEGER NOT NULL DEFAULT 0,
                parser_confidence REAL,
                parsed_show_title TEXT,
                parsed_episode_number INTEGER,
                parser_method TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS shows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                normalized_title TEXT NOT NULL UNIQUE,
                aliases TEXT NOT NULL DEFAULT '[]',
                latest_episode INTEGER,
                latest_update_at TEXT,
                status TEXT NOT NULL DEFAULT 'updating',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (status IN ('updating', 'completed', 'paused'))
            );

            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                episode_number INTEGER NOT NULL,
                first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
                first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                published_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(show_id, episode_number),
                CHECK (episode_number > 0)
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

            CREATE INDEX IF NOT EXISTS idx_accounts_due
                ON accounts(enabled, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_videos_account_created
                ON videos(account_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_videos_review
                ON videos(needs_review, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_episodes_show_number
                ON episodes(show_id, episode_number DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_episode
                ON notifications(episode_id, sent_at DESC);
            """
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
        nickname = str(raw_user.get("nickname") or sec_uid[:12]).strip() or sec_uid[:12]
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
        safe_nickname = nickname.strip() or safe_sec_uid[:12]
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
    ) -> None:
        """Refresh cached show metadata after destructive source changes."""
        if not show_ids:
            return
        now = utc_now()
        for show_id in show_ids:
            latest = connection.execute(
                """
                SELECT episode_number, COALESCE(published_at, created_at) AS latest_update_at
                FROM episodes
                WHERE show_id = ?
                ORDER BY episode_number DESC, COALESCE(published_at, created_at) DESC, id DESC
                LIMIT 1
                """,
                (show_id,),
            ).fetchone()
            if latest is None:
                connection.execute(
                    """
                    UPDATE shows
                    SET latest_episode = NULL, latest_update_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, show_id),
                )
                continue
            connection.execute(
                """
                UPDATE shows
                SET latest_episode = ?, latest_update_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(latest["episode_number"]), latest["latest_update_at"], now, show_id),
            )

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
                    cover_url, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM videos WHERE aweme_id = ?", (safe_aweme_id,)).fetchone()
            if row is None:
                raise RuntimeError("保存视频后无法读取记录")
            return _video_row(row), cursor.rowcount == 1

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
        parsed_episode_number: int | None = None,
        parser_method: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE videos SET
                    is_processed = ?, needs_review = ?, parser_confidence = ?,
                    parsed_show_title = ?, parsed_episode_number = ?, parser_method = ?,
                    processed_at = ?
                WHERE id = ?
                """,
                (
                    int(is_processed),
                    int(needs_review),
                    parser_confidence,
                    _optional_text(parsed_show_title),
                    parsed_episode_number,
                    _optional_text(parser_method),
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
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        query = """
            SELECT videos.*, accounts.nickname AS account_nickname, accounts.sec_uid AS account_sec_uid
            FROM videos JOIN accounts ON accounts.id = videos.account_id
        """
        params: list[Any] = []
        if needs_review is not None:
            query += " WHERE videos.needs_review = ?"
            params.append(int(needs_review))
        query += " ORDER BY COALESCE(videos.publish_time, videos.created_at) DESC LIMIT ?"
        params.append(safe_limit)
        with self._transaction() as connection:
            return [_video_row(row) for row in connection.execute(query, params).fetchall()]

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

    def list_shows(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM shows ORDER BY latest_update_at DESC, updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
            return [_show_row(row) for row in rows]

    def list_show_summaries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return dashboard rows with the first source of each latest episode."""
        safe_limit = max(1, min(int(limit), 500))
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT
                    shows.*,
                    latest_episode.first_account_id AS latest_account_id,
                    accounts.nickname AS latest_account_nickname,
                    latest_video.video_url AS latest_video_url,
                    latest_video.cover_url AS latest_cover_url
                FROM shows
                LEFT JOIN episodes AS latest_episode
                    ON latest_episode.id = (
                        SELECT episodes_inner.id
                        FROM episodes AS episodes_inner
                        WHERE episodes_inner.show_id = shows.id
                        ORDER BY episodes_inner.episode_number DESC
                        LIMIT 1
                    )
                LEFT JOIN accounts ON accounts.id = latest_episode.first_account_id
                LEFT JOIN videos AS latest_video ON latest_video.id = latest_episode.first_video_id
                ORDER BY shows.latest_update_at DESC, shows.updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return [_show_row(row) for row in rows]

    def get_show_detail(self, show_id: int) -> dict[str, Any] | None:
        show = self.get_show(show_id)
        if show is None:
            return None
        episodes = self.get_show_episodes(show_id)
        for episode in episodes:
            episode["sources"] = self.get_episode_sources(int(episode["id"]))
        show["episodes"] = episodes
        return show

    def list_show_candidates(self) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM shows ORDER BY title COLLATE NOCASE").fetchall()
            return [_show_row(row) for row in rows]

    def update_show(
        self,
        show_id: int,
        *,
        title: str | None = None,
        aliases: Sequence[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if title is not None:
            cleaned_title = title.strip()
            if not cleaned_title:
                raise ValueError("剧名不能为空")
            changes["title"] = cleaned_title
        if aliases is not None:
            changes["aliases"] = _json_array(aliases)
        if status is not None:
            if status not in SHOW_STATUSES:
                raise ValueError("短剧状态无效")
            changes["status"] = status
        if not changes:
            existing = self.get_show(show_id)
            if existing is None:
                raise KeyError("短剧不存在")
            return existing
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        with self._transaction() as connection:
            cursor = connection.execute(
                f"UPDATE shows SET {assignments} WHERE id = ?", (*changes.values(), show_id)
            )
            if cursor.rowcount == 0:
                raise KeyError("短剧不存在")
            row = connection.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            return _show_row(row)

    def record_episode_source(
        self,
        *,
        show_id: int,
        episode_number: int,
        video_id: int,
        account_id: str,
        published_at: str | None,
    ) -> EpisodeWriteResult:
        if episode_number <= 0:
            raise ValueError("集数必须大于 0")
        now = utc_now()
        with self._transaction() as connection:
            if connection.execute("SELECT 1 FROM shows WHERE id = ?", (show_id,)).fetchone() is None:
                raise KeyError("短剧不存在")
            existing_episode = connection.execute(
                "SELECT * FROM episodes WHERE show_id = ? AND episode_number = ?",
                (show_id, episode_number),
            ).fetchone()
            is_new_episode = existing_episode is None
            if existing_episode is None:
                cursor = connection.execute(
                    """
                    INSERT INTO episodes(
                        show_id, episode_number, first_video_id, first_account_id, published_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (show_id, episode_number, video_id, account_id, _optional_text(published_at), now),
                )
                episode_id = int(cursor.lastrowid)
                episode_row = connection.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
                current_show = connection.execute("SELECT latest_episode FROM shows WHERE id = ?", (show_id,)).fetchone()
                latest_episode = current_show["latest_episode"] if current_show else None
                if latest_episode is None or episode_number >= int(latest_episode):
                    connection.execute(
                        """
                        UPDATE shows
                        SET latest_episode = ?, latest_update_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (episode_number, _optional_text(published_at) or now, now, show_id),
                    )
            else:
                episode_row = existing_episode
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
            return EpisodeWriteResult(
                episode=_episode_row(episode_row),
                source=_episode_source_row(source_row),
                is_new_episode=is_new_episode,
                is_new_source=source_cursor.rowcount == 1,
            )

    def get_show_episodes(self, show_id: int) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM episodes WHERE show_id = ? ORDER BY episode_number DESC", (show_id,)
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
                "pending_review": _count(connection, "videos WHERE needs_review = 1"),
            }

    def system_status(self) -> dict[str, Any]:
        with self._transaction() as connection:
            counts = {
                "accounts": _count(connection, "accounts"),
                "enabled_accounts": _count(connection, "accounts WHERE enabled = 1"),
                "shows": _count(connection, "shows"),
                "videos": _count(connection, "videos"),
                "pending_review": _count(connection, "videos WHERE needs_review = 1"),
            }
            last_checked = connection.execute("SELECT MAX(last_checked_at) FROM accounts").fetchone()[0]
            errors = connection.execute(
                """
                SELECT id, nickname, last_error, last_checked_at, consecutive_failures
                FROM accounts
                WHERE last_error IS NOT NULL AND last_error != ''
                ORDER BY last_checked_at DESC
                LIMIT 10
                """
            ).fetchall()
            return {
                **counts,
                "last_check_at": last_checked,
                "recent_errors": [_account_row(row) for row in errors],
            }

    def _require_account(self, connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError("账号不存在")
        return _account_row(row)


def _count(connection: sqlite3.Connection, table_or_clause: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table_or_clause}").fetchone()[0])


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


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _account_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["enabled"] = bool(result["enabled"])
    result["initial_sync_completed"] = bool(result["initial_sync_completed"])
    return result


def _video_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["hashtags"] = _json_list(result.get("hashtags"))
    result["is_processed"] = bool(result["is_processed"])
    result["needs_review"] = bool(result["needs_review"])
    return result


def _show_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["aliases"] = _json_list(result.get("aliases"))
    return result


def _episode_row(row: sqlite3.Row) -> dict[str, Any]:
    return _row_to_dict(row)


def _episode_source_row(row: sqlite3.Row) -> dict[str, Any]:
    return _row_to_dict(row)


def _notification_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _row_to_dict(row)
    result["success"] = bool(result["success"])
    return result
