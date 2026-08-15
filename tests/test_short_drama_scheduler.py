from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.scheduler import (
    AccountScheduler,
    SchedulerConfig,
    calculate_backoff_minutes,
)


@dataclass(frozen=True)
class StubSyncResult:
    account: dict


class StubPipeline:
    def __init__(self, *, failures: set[str] | None = None) -> None:
        self.failures = failures or set()
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def sync_account(self, account_id: str):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(account_id)
        try:
            await asyncio.sleep(0.01)
            if account_id in self.failures:
                raise RuntimeError("login_required")
            return StubSyncResult(account={"id": account_id})
        finally:
            self.active -= 1


class AccountSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")
        self.now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_account(self, sec_uid: str) -> dict:
        return self.repository.create_account(
            sec_uid=sec_uid,
            nickname=sec_uid,
            homepage_url=f"https://www.douyin.com/user/{sec_uid}",
            check_interval_minutes=10,
        )

    async def test_due_accounts_are_limited_by_concurrency_and_rescheduled_with_jitter(self):
        first = self.add_account("first")
        second = self.add_account("second")
        pipeline = StubPipeline()
        scheduler = AccountScheduler(
            repository=self.repository,
            pipeline=pipeline,
            config=SchedulerConfig(max_concurrent_checks=1, jitter_ratio=0, poll_seconds=1),
            now=lambda: self.now,
            jitter=lambda low, high: 0,
        )

        results = await scheduler.run_due_once()

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.success for result in results))
        self.assertEqual(pipeline.max_active, 1)
        self.assertEqual(
            self.repository.get_account(first["id"])["next_check_at"],
            "2026-08-15T12:10:00+00:00",
        )
        self.assertEqual(
            self.repository.get_account(second["id"])["next_check_at"],
            "2026-08-15T12:10:00+00:00",
        )

    async def test_failures_use_exponential_backoff_without_blocking_other_accounts(self):
        failing = self.add_account("failing")
        healthy = self.add_account("healthy")
        pipeline = StubPipeline(failures={failing["id"]})
        scheduler = AccountScheduler(
            repository=self.repository,
            pipeline=pipeline,
            config=SchedulerConfig(max_concurrent_checks=2, max_backoff_minutes=60, jitter_ratio=0, poll_seconds=1),
            now=lambda: self.now,
        )

        results = await scheduler.run_due_once()

        by_id = {result.account_id: result for result in results}
        self.assertFalse(by_id[failing["id"]].success)
        self.assertTrue(by_id[healthy["id"]].success)
        failed_account = self.repository.get_account(failing["id"])
        self.assertEqual(failed_account["consecutive_failures"], 1)
        self.assertEqual(failed_account["next_check_at"], "2026-08-15T12:10:00+00:00")
        self.assertEqual(calculate_backoff_minutes(interval_minutes=10, consecutive_failures=1, max_backoff_minutes=60), 20)


if __name__ == "__main__":
    unittest.main()
