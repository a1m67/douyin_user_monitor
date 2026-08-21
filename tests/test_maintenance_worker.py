from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.maintenance_worker import MaintenanceWorker, MaintenanceWorkerConfig


class MaintenanceWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repository = ShortDramaRepository(Path(self.temp.name) / "app.db")

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_run_once_creates_due_backup_and_passive_checkpoint(self):
        account = self.repository.create_account(
            sec_uid="maintenance-raw",
            nickname="维护测试",
            homepage_url="https://www.douyin.com/user/maintenance-raw",
        )
        video, _ = self.repository.create_video(
            aweme_id="maintenance-raw-1",
            account_id=account["id"],
            description="《维护测试》第1集",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/maintenance-raw-1",
            cover_url=None,
            raw={},
        )
        connection = sqlite3.connect(self.repository.database_path)
        try:
            connection.execute(
                "UPDATE videos SET raw_json=? WHERE id=?",
                (json.dumps({"desc": "《维护测试》第1集", "video": {"payload": "x" * 5000}}), video["id"]),
            )
            connection.commit()
        finally:
            connection.close()
        worker = MaintenanceWorker(
            self.repository,
            MaintenanceWorkerConfig(backup_retention_count=2, backup_interval_hours=24),
        )
        result = await worker.run_once(now=datetime(2026, 8, 22, tzinfo=timezone.utc))
        self.assertIsNotNone(result["backup"])
        self.assertIsNotNone(result["checkpoint"])
        self.assertEqual(result["raw_payloads_compacted"], 1)
        self.assertTrue((self.repository.database_path.parent / "backups" / result["backup"]).is_file())
        self.assertIsNotNone(worker.health_status()["last_backup_at"])

    async def test_recent_backup_is_not_duplicated_until_due(self):
        worker = MaintenanceWorker(
            self.repository,
            MaintenanceWorkerConfig(backup_interval_hours=24, checkpoint_interval_hours=24),
        )
        now = datetime.now(timezone.utc)
        first = await worker.run_once(now=now, force_backup=True)
        second = await worker.run_once(now=now + timedelta(hours=1))
        self.assertIsNotNone(first["backup"])
        self.assertIsNone(second["backup"])

    async def test_disabled_worker_does_not_start_background_task(self):
        worker = MaintenanceWorker(self.repository, MaintenanceWorkerConfig(enabled=False))
        await worker.start()
        self.assertFalse(worker.health_status()["running"])
        await worker.stop()
