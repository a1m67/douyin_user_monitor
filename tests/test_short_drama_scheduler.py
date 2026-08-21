from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.crawler_circuit_breaker import CrawlerCircuitBreaker
from douyin_user_monitor.services.scheduler import (
    AccountScheduler,
    SchedulerConfig,
    calculate_backoff_minutes,
)


@dataclass(frozen=True)
class StubSyncResult:
    account: dict
    regex_calls: int = 0
    context_calls: int = 0
    llm_calls: int = 0
    ocr_calls: int = 0
    ocr_successes: int = 0
    llm_latency_ms_total: int = 0
    ocr_latency_ms_total: int = 0


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


class ControlledPipeline(StubPipeline):
    def __init__(self, blocked_account_id: str) -> None:
        super().__init__()
        self.blocked_account_id = blocked_account_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active_by_account: dict[str, int] = {}
        self.max_active_by_account: dict[str, int] = {}

    async def sync_account(self, account_id: str):
        self.calls.append(account_id)
        self.active_by_account[account_id] = self.active_by_account.get(account_id, 0) + 1
        self.max_active_by_account[account_id] = max(
            self.max_active_by_account.get(account_id, 0),
            self.active_by_account[account_id],
        )
        try:
            if account_id == self.blocked_account_id:
                self.started.set()
                await self.release.wait()
            return StubSyncResult(account={"id": account_id})
        finally:
            self.active_by_account[account_id] -= 1


class MetricsPipeline(StubPipeline):
    async def sync_account(self, account_id: str):
        return StubSyncResult(
            account={"id": account_id},
            regex_calls=4,
            context_calls=2,
            llm_calls=1,
            ocr_calls=1,
            ocr_successes=1,
            llm_latency_ms_total=17,
            ocr_latency_ms_total=23,
        )


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
        self.assertEqual(self.repository.list_scan_runs(failing["id"])[0]["success"], 0)
        self.assertEqual(self.repository.list_scan_runs(healthy["id"])[0]["trigger_type"], "scheduler")

    async def test_manual_run_is_recorded_with_manual_trigger(self):
        account = self.add_account("manual")
        scheduler = AccountScheduler(repository=self.repository, pipeline=StubPipeline(), config=SchedulerConfig(), now=lambda: self.now)
        await scheduler.run_account_once(account["id"])
        self.assertEqual(self.repository.list_scan_runs(account["id"])[0]["trigger_type"], "manual")

    async def test_scan_run_persists_real_parser_metrics(self):
        account = self.add_account("metrics")
        scheduler = AccountScheduler(repository=self.repository, pipeline=MetricsPipeline(), config=SchedulerConfig(), now=lambda: self.now)
        await scheduler.run_account_once(account["id"])
        scan = self.repository.list_scan_runs(account["id"])[0]
        self.assertEqual(
            (scan["regex_calls"], scan["context_calls"], scan["llm_calls"], scan["ocr_calls"]),
            (4, 2, 1, 1),
        )
        self.assertEqual((scan["llm_latency_ms_total"], scan["ocr_latency_ms_total"]), (17, 23))

    async def test_manual_account_a_does_not_block_account_b(self):
        first = self.add_account("manual-a")
        second = self.add_account("manual-b")
        pipeline = ControlledPipeline(first["id"])
        scheduler = AccountScheduler(repository=self.repository, pipeline=pipeline, config=SchedulerConfig(), now=lambda: self.now)
        first_task = asyncio.create_task(scheduler.run_account_once(first["id"]))
        await pipeline.started.wait()
        second_result = await asyncio.wait_for(scheduler.run_account_once(second["id"]), timeout=0.5)
        self.assertTrue(second_result.success)
        pipeline.release.set()
        await first_task

    async def test_concurrent_manual_runs_for_same_account_are_serialized_and_cleaned_up(self):
        account = self.add_account("manual-same")
        pipeline = ControlledPipeline(account["id"])
        scheduler = AccountScheduler(repository=self.repository, pipeline=pipeline, config=SchedulerConfig(), now=lambda: self.now)
        first_task = asyncio.create_task(scheduler.run_account_once(account["id"]))
        await pipeline.started.wait()
        second_task = asyncio.create_task(scheduler.run_account_once(account["id"]))
        await asyncio.sleep(0)
        self.assertEqual(pipeline.max_active_by_account[account["id"]], 1)
        pipeline.release.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(pipeline.max_active_by_account[account["id"]], 1)
        self.assertEqual(scheduler._account_locks, {})

    async def test_open_circuit_skips_pipeline_until_half_open_probe_succeeds(self):
        accounts = [self.add_account(name) for name in ("one", "two", "three", "four")]
        pipeline = StubPipeline(failures={item["id"] for item in accounts[:3]})
        breaker = CrawlerCircuitBreaker(
            failure_threshold=3, open_minutes=20, now=lambda: self.now
        )
        scheduler = AccountScheduler(
            repository=self.repository,
            pipeline=pipeline,
            config=SchedulerConfig(jitter_ratio=0, poll_seconds=1),
            circuit_breaker=breaker,
            now=lambda: self.now,
        )
        for account in accounts[:3]:
            await scheduler.run_account_once(account["id"])

        blocked = await scheduler.run_account_once(accounts[3]["id"])
        self.assertTrue(blocked.circuit_open)
        self.assertNotIn(accounts[3]["id"], pipeline.calls)
        self.now += timedelta(minutes=20)
        probe = await scheduler.run_account_once(accounts[3]["id"])
        self.assertTrue(probe.success)
        self.assertEqual(scheduler.crawler_status()["state"], "closed")


if __name__ == "__main__":
    unittest.main()
