import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from douyin_user_monitor.__main__ import main
from douyin_user_monitor.maintenance import (
    backup_database,
    database_stats,
    doctor_database,
    restore_database,
    verify_backup,
)
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "app.db"
        self.repository = ShortDramaRepository(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_is_openable_and_retention_only_removes_named_backups(self):
        keep = self.root / "backups" / "unrelated.db"
        keep.parent.mkdir()
        keep.write_bytes(b"keep")
        first = backup_database(self.database, retention_count=1)
        second = backup_database(self.database, retention_count=1)
        self.assertNotEqual(first, second)
        self.assertTrue(keep.exists())
        connection = sqlite3.connect(second)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        manifest = json.loads(second.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["filename"], second.name)
        self.assertEqual(manifest["size_bytes"], second.stat().st_size)
        self.assertEqual(manifest["schema_version"], self.repository.schema_version())
        self.assertEqual(len(manifest["sha256"]), 64)
        self.assertTrue(verify_backup(second)["ok"])

    def test_verify_supports_legacy_backup_and_rejects_corruption_or_bad_hash(self):
        backup = backup_database(self.database)
        legacy = self.root / "legacy.db"
        legacy.write_bytes(backup.read_bytes())
        self.assertTrue(verify_backup(legacy)["ok"])

        manifest_path = backup.with_suffix(".json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(verify_backup(backup)["checks"]["hash"], "mismatch")

        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        self.assertFalse(verify_backup(corrupt)["ok"])

    def test_restore_dry_run_success_older_schema_and_future_schema_rejection(self):
        account = self.repository.create_account(
            sec_uid="restore-source", nickname="restore-source", homepage_url="url"
        )
        backup = backup_database(self.database)
        self.repository.update_account(account["id"], nickname="changed")

        dry_run = restore_database(
            self.database, source_path=backup, dry_run=True
        )
        self.assertTrue(dry_run["dry_run"])
        self.assertEqual(self.repository.get_account(account["id"])["nickname"], "changed")

        older = self.root / "older.db"
        older.write_bytes(backup.read_bytes())
        connection = sqlite3.connect(older)
        try:
            connection.execute("UPDATE app_meta SET value='20' WHERE key='schema_version'")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(verify_backup(older)["schema_version"], 20)
        self.assertTrue(
            restore_database(self.database, source_path=older, dry_run=True)["ok"]
        )

        future = self.root / "future.db"
        future.write_bytes(backup.read_bytes())
        connection = sqlite3.connect(future)
        try:
            connection.execute("UPDATE app_meta SET value='999' WHERE key='schema_version'")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "高于当前程序"):
            restore_database(self.database, source_path=future, dry_run=True)

    def test_restore_success_requires_confirmation_and_refuses_active_writer(self):
        account = self.repository.create_account(
            sec_uid="restore-success", nickname="before", homepage_url="url"
        )
        backup = backup_database(self.database)
        self.repository.update_account(account["id"], nickname="after")
        with self.assertRaisesRegex(ValueError, "--yes"):
            restore_database(self.database, source_path=backup)

        lock = sqlite3.connect(self.database)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(RuntimeError, "停止 Docker/Application"):
                restore_database(self.database, source_path=backup, confirmed=True)
        finally:
            lock.rollback()
            lock.close()

        restored = restore_database(
            self.database, source_path=backup, confirmed=True
        )
        self.assertTrue(restored["ok"])
        self.assertIsNotNone(restored["pre_restore_backup"])
        reopened = ShortDramaRepository(self.database)
        self.assertEqual(reopened.get_account(account["id"])["nickname"], "before")

    def test_restore_validation_failure_rolls_back_current_database(self):
        account = self.repository.create_account(
            sec_uid="restore-rollback", nickname="backup-state", homepage_url="url"
        )
        source = backup_database(self.database)
        self.repository.update_account(account["id"], nickname="current-state")
        real_verify = verify_backup

        def fail_post_restore(path, *, verify_hash=True):
            if Path(path).resolve() == self.database.resolve() and not verify_hash:
                return {
                    "ok": False,
                    "error": "forced post-restore failure",
                    "checks": {},
                    "schema_version": 21,
                }
            return real_verify(Path(path), verify_hash=verify_hash)

        with patch("douyin_user_monitor.maintenance.verify_backup", side_effect=fail_post_restore):
            with self.assertRaisesRegex(RuntimeError, "恢复后数据库验证失败"):
                restore_database(self.database, source_path=source, confirmed=True)

        reopened = ShortDramaRepository(self.database)
        self.assertEqual(reopened.get_account(account["id"])["nickname"], "current-state")

    def test_doctor_passes_normal_database_and_repairs_stale_show_summary(self):
        self.assertTrue(doctor_database(self.database).ok)
        account = self.repository.create_account(sec_uid="doctor", nickname="doctor", homepage_url="url")
        video, _ = self.repository.create_video(aweme_id="doctor-1", account_id=account["id"], description="", hashtags=[], publish_time=None, video_url="", cover_url=None, raw={})
        show = self.repository.create_show(title="Doctor", normalized_title="doctor")
        self.repository.record_episode_source(show_id=show["id"], episode_number=1, video_id=video["id"], account_id=account["id"], published_at=None)
        with self.repository._transaction() as connection:
            connection.execute("UPDATE shows SET latest_episode=99 WHERE id=?", (show["id"],))
        self.assertFalse(doctor_database(self.database).ok)
        self.assertTrue(doctor_database(self.database, repair=True).ok)

    def test_database_stats_reports_wal_rows_indexes_and_explicit_checkpoint(self):
        account = self.repository.create_account(
            sec_uid="stats", nickname="stats", homepage_url="url"
        )
        report = database_stats(self.database, checkpoint=True)
        self.assertEqual(report["journal_mode"], "wal")
        self.assertEqual(report["table_rows"]["accounts"], 1)
        self.assertEqual(report["table_rows"]["update_events"], 0)
        self.assertGreater(report["database_size_bytes"], 0)
        self.assertIsNotNone(report["checkpoint"])
        self.assertIn(
            "idx_update_events_unread_order",
            {index["name"] for index in report["indexes"]},
        )
        self.assertEqual(self.repository.get_account(account["id"])["nickname"], "stats")

    def test_db_stats_cli_prints_json_report(self):
        output = StringIO()
        settings = SimpleNamespace(database_path=self.database, backup_retention_count=14)
        with (
            patch("sys.argv", ["douyin_user_monitor", "db-stats"]),
            patch(
                "douyin_user_monitor.__main__.load_short_drama_settings",
                return_value=settings,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertIn('"journal_mode": "wal"', output.getvalue())
        self.assertIn('"table_rows"', output.getvalue())

    def test_search_rebuild_cli_reports_index_or_like_fallback(self):
        output = StringIO()
        settings = SimpleNamespace(database_path=self.database, backup_retention_count=14)
        with (
            patch("sys.argv", ["douyin_user_monitor", "search-rebuild"]),
            patch(
                "douyin_user_monitor.__main__.load_short_drama_settings",
                return_value=settings,
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        report = json.loads(output.getvalue())
        self.assertIn(report["mode"], {"fts5", "like"})
        if report["mode"] == "fts5":
            self.assertTrue(report["rebuilt"])
            self.assertTrue(report["consistent"])

    def test_backup_verify_and_restore_dry_run_cli(self):
        backup = backup_database(self.database)
        settings = SimpleNamespace(database_path=self.database, backup_retention_count=14)
        output = StringIO()
        with (
            patch("sys.argv", ["douyin_user_monitor", "backup-verify", "--file", str(backup)]),
            patch("douyin_user_monitor.__main__.load_short_drama_settings", return_value=settings),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])

        output = StringIO()
        with (
            patch("sys.argv", ["douyin_user_monitor", "restore", "--from", str(backup), "--dry-run"]),
            patch("douyin_user_monitor.__main__.load_short_drama_settings", return_value=settings),
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 0)
        self.assertTrue(json.loads(output.getvalue())["dry_run"])


if __name__ == "__main__":
    unittest.main()
