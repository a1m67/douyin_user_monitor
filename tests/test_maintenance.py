import sqlite3
import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.maintenance import backup_database, doctor_database
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


if __name__ == "__main__":
    unittest.main()
