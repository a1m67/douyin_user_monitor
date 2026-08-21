from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.providers.base import (
    ProviderAccount,
    ProviderVideo,
    ProviderVideoPage,
)
from douyin_user_monitor.providers.fake import FakeDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.services.history_backfill_worker import (
    HistoryBackfillWorker,
    HistoryBackfillWorkerConfig,
)


def video(aweme_id: str, episode: int) -> ProviderVideo:
    return ProviderVideo(
        aweme_id=aweme_id,
        description=f"《归墟》第{episode}集",
        hashtags=("归墟",),
        publish_time=f"2026-08-15T12:{episode:02d}:00+00:00",
        video_url=f"https://www.douyin.com/video/{aweme_id}",
        cover_url=None,
        raw={"aweme_id": aweme_id},
    )


def page(aweme_id: str, episode: int, next_cursor: int, has_more: bool) -> ProviderVideoPage:
    return ProviderVideoPage(
        videos=(video(aweme_id, episode),),
        next_cursor=next_cursor,
        has_more=has_more,
    )


class RecordingDispatcher:
    def __init__(self) -> None:
        self.updates = []

    async def dispatch(self, update) -> None:
        self.updates.append(update)


class FailOnceProvider(FakeDouyinProvider):
    def __init__(self, *, fail_cursor: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_cursor = fail_cursor
        self.failed = False

    async def get_video_page(self, account, *, cursor: int, limit: int):
        if cursor == self.fail_cursor and not self.failed:
            self.failed = True
            self.page_calls.append((account.sec_uid, cursor, limit))
            raise RuntimeError("temporary page failure token=secret")
        return await super().get_video_page(account, cursor=cursor, limit=limit)


class SlowProvider(FakeDouyinProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.active = 0
        self.peak_active = 0

    async def get_video_page(self, account, *, cursor: int, limit: int):
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(0.03)
            return await super().get_video_page(account, cursor=cursor, limit=limit)
        finally:
            self.active -= 1


class TransientThenSuccessProvider(FakeDouyinProvider):
    def __init__(self, *, transient_count: int, success_page: ProviderVideoPage, **kwargs) -> None:
        super().__init__(**kwargs)
        self.transient_count = transient_count
        self.success_page = success_page

    async def get_video_page(self, account, *, cursor: int, limit: int):
        self.page_calls.append((account.sec_uid, cursor, limit))
        if len(self.page_calls) <= self.transient_count:
            return ProviderVideoPage(videos=(), next_cursor=cursor, has_more=True)
        return self.success_page


class PausableSleep:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        _ = seconds
        if not self.started.is_set():
            self.started.set()
            await self.release.wait()


class HistoryBackfillWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")
        self.workers: list[HistoryBackfillWorker] = []

    async def asyncTearDown(self) -> None:
        for worker in self.workers:
            await worker.stop()
        self.temp_dir.cleanup()

    def account(self, sec_uid: str = "sec-one") -> dict:
        return self.repository.create_account(
            sec_uid=sec_uid,
            nickname=sec_uid,
            homepage_url=f"https://www.douyin.com/user/{sec_uid}",
            check_interval_minutes=10,
        )

    def build(
        self,
        provider: FakeDouyinProvider,
        *,
        dispatcher=None,
        sleep=asyncio.sleep,
        concurrency: int = 1,
    ) -> tuple[ShortDramaPipeline, HistoryBackfillWorker]:
        pipeline = ShortDramaPipeline(
            repository=self.repository,
            provider=provider,
            dispatcher=dispatcher,
            history_backfill_page_size=20,
        )
        worker = HistoryBackfillWorker(
            repository=self.repository,
            pipeline=pipeline,
            config=HistoryBackfillWorkerConfig(
                max_concurrent_backfills=concurrency,
                delay_min_seconds=0,
                delay_max_seconds=0,
                retry_delays_seconds=(0, 0),
                poll_seconds=0.01,
            ),
            sleep=sleep,
            jitter=lambda _minimum, _maximum: 0,
        )
        self.workers.append(worker)
        return pipeline, worker

    async def test_start_runs_all_pages_and_never_sends_notifications(self):
        account = self.account()
        dispatcher = RecordingDispatcher()
        provider = FakeDouyinProvider(
            video_pages_by_sec_uid={
                "sec-one": {
                    0: page("v1", 1, 20, True),
                    20: page("v2", 2, 40, True),
                    40: page("v3", 3, 60, False),
                }
            }
        )
        pipeline, worker = self.build(provider, dispatcher=dispatcher)

        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_sync_status"], "completed")
        self.assertEqual(stored["history_processed_pages"], 3)
        self.assertEqual(stored["history_scanned_items"], 3)
        self.assertEqual(stored["history_new_videos"], 3)
        self.assertEqual([call[1] for call in provider.page_calls], [0, 20, 40])
        self.assertEqual(dispatcher.updates, [])

    async def test_pause_stops_before_next_page_and_resume_uses_saved_cursor(self):
        account = self.account()
        provider = FakeDouyinProvider(
            video_pages_by_sec_uid={
                "sec-one": {
                    0: page("v1", 1, 20, True),
                    20: page("v2", 2, 40, False),
                }
            }
        )
        delay = PausableSleep()
        pipeline, worker = self.build(provider, sleep=delay)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await asyncio.wait_for(delay.started.wait(), timeout=2)

        pipeline.pause_history_backfill(account["id"])
        delay.release.set()
        await asyncio.sleep(0.05)
        self.assertEqual([call[1] for call in provider.page_calls], [0])
        self.assertEqual(self.repository.get_account(account["id"])["history_next_cursor"], 20)

        pipeline.resume_history_backfill(account["id"])
        worker.wake()
        await worker.wait_until_idle()
        self.assertEqual([call[1] for call in provider.page_calls], [0, 20])
        self.assertEqual(self.repository.get_account(account["id"])["history_sync_status"], "completed")

    async def test_page_failure_is_retried_and_can_complete(self):
        account = self.account()
        provider = FailOnceProvider(
            fail_cursor=20,
            video_pages_by_sec_uid={
                "sec-one": {
                    0: page("v1", 1, 20, True),
                    20: page("v2", 2, 40, False),
                }
            },
        )
        pipeline, worker = self.build(provider)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_sync_status"], "completed")
        self.assertEqual([call[1] for call in provider.page_calls], [0, 20, 20])
        self.assertIsNone(stored["history_last_error"])

    async def test_transient_empty_retries_without_advancing_cursor_then_succeeds(self):
        account = self.account()
        provider = TransientThenSuccessProvider(
            transient_count=1,
            success_page=page("v1", 1, 20, False),
        )
        pipeline, worker = self.build(provider)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_sync_status"], "completed")
        self.assertEqual(stored["history_processed_pages"], 1)
        self.assertEqual([call[1] for call in provider.page_calls], [0, 0])

    async def test_three_transient_empty_pages_fail_without_advancing_cursor(self):
        account = self.account()
        provider = TransientThenSuccessProvider(
            transient_count=3,
            success_page=page("unused", 1, 20, False),
        )
        pipeline, worker = self.build(provider)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_sync_status"], "failed")
        self.assertEqual(stored["history_next_cursor"], 0)
        self.assertEqual(stored["history_processed_pages"], 0)
        self.assertEqual(len(provider.page_calls), 3)

    async def test_startup_recovers_running_job_from_persisted_cursor(self):
        account = self.account()
        started = self.repository.start_history_backfill(account["id"])
        self.repository.update_history_sync_state(
            account["id"],
            status="running",
            next_cursor=20,
            has_more=True,
            processed_pages=1,
            scanned_items=1,
            new_videos=1,
            started_at=started["history_started_at"],
            cursor_history=[0, 20],
        )
        provider = FakeDouyinProvider(
            video_pages_by_sec_uid={"sec-one": {20: page("v2", 2, 40, False)}}
        )
        _pipeline, worker = self.build(provider)

        await worker.start()
        await worker.wait_until_idle()

        self.assertEqual([call[1] for call in provider.page_calls], [20])
        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_processed_pages"], 2)
        self.assertEqual(stored["history_sync_status"], "completed")

    async def test_cursor_may_decrease_but_must_not_repeat(self):
        account = self.account()
        provider = FakeDouyinProvider(
            video_pages_by_sec_uid={
                "sec-one": {
                    0: page("v1", 1, 100, True),
                    100: page("v2", 2, 50, True),
                    50: page("v3", 3, 25, False),
                }
            }
        )
        pipeline, worker = self.build(provider)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        self.assertEqual([call[1] for call in provider.page_calls], [0, 100, 50])
        self.assertEqual(self.repository.get_account(account["id"])["history_sync_status"], "completed")

    async def test_repeated_cursor_fails_after_three_attempts_and_preserves_cursor(self):
        account = self.account()
        provider = FakeDouyinProvider(
            video_pages_by_sec_uid={"sec-one": {0: page("v1", 1, 0, True)}}
        )
        pipeline, worker = self.build(provider)
        pipeline.start_history_backfill(account["id"])
        await worker.start()
        await worker.wait_until_idle()

        stored = self.repository.get_account(account["id"])
        self.assertEqual(stored["history_sync_status"], "failed")
        self.assertEqual(stored["history_next_cursor"], 0)
        self.assertIn("游标重复", stored["history_last_error"])
        self.assertEqual(len(provider.page_calls), 3)

    async def test_backfill_concurrency_limit_is_separate_and_enforced(self):
        first = self.account("sec-one")
        second = self.account("sec-two")
        provider = SlowProvider(
            video_pages_by_sec_uid={
                "sec-one": {0: page("v1", 1, 20, False)},
                "sec-two": {0: page("v2", 2, 20, False)},
            }
        )
        pipeline, worker = self.build(provider, concurrency=1)
        pipeline.start_history_backfill(first["id"])
        pipeline.start_history_backfill(second["id"])
        await worker.start()
        await worker.wait_until_idle()

        self.assertEqual(provider.peak_active, 1)
        self.assertEqual(self.repository.get_account(first["id"])["history_sync_status"], "completed")
        self.assertEqual(self.repository.get_account(second["id"])["history_sync_status"], "completed")
