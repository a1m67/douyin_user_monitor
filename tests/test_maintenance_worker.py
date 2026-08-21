from __future__ import annotations

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
        worker = MaintenanceWorker(
            self.repository,
            MaintenanceWorkerConfig(backup_retention_count=2, backup_interval_hours=24),
        )
        result = await worker.run_once(now=datetime(2026, 8, 22, tzinfo=timezone.utc))
        self.assertIsNotNone(result["backup"])
        self.assertIsNotNone(result["checkpoint"])
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
