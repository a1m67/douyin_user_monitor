from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from douyin_user_monitor.ocr import FakeOCRBackend
from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.llm import LLMParser, LLMTimeoutError
from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile, ProviderVideo
from douyin_user_monitor.providers.fake import FakeDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.ai_request_guard import (
    AIRequestGuard,
    AIRequestUnavailable,
    GuardedLLMParser,
    GuardedOCRBackend,
)
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline


class FakeLLMClient:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, input_payload):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def llm_decision(show_id: int) -> str:
    return json.dumps({
        "is_episode": True,
        "show_title": "归墟",
        "show_id": show_id,
        "episode_number": 9,
        "content_type": "episode",
        "confidence": 0.98,
        "reason": "resolved",
    })


class AIRequestGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unlimited_budget_records_every_call(self):
        guard = AIRequestGuard(
            self.repository, provider="llm", max_concurrent_requests=2, daily_call_limit=0
        )
        self.assertEqual(guard.call(lambda: "ok"), "ok")
        self.assertEqual(guard.call(lambda: "ok"), "ok")
        usage = self.repository.ai_usage_snapshot(daily_limits={"llm": 0})["llm"]
        self.assertEqual((usage["calls"], usage["successes"], usage["failures"]), (2, 2, 0))

    def test_budget_reaches_limit_and_resets_on_next_utc_date(self):
        current = [datetime(2026, 8, 22, 23, 59, tzinfo=timezone.utc)]
        guard = AIRequestGuard(
            self.repository,
            provider="llm",
            max_concurrent_requests=1,
            daily_call_limit=1,
            clock=lambda: current[0],
        )
        guard.call(lambda: "first")
        with self.assertRaisesRegex(AIRequestUnavailable, "llm_budget_exhausted"):
            guard.call(lambda: "blocked")
        current[0] = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(guard.call(lambda: "next-day"), "next-day")
        self.assertEqual(
            self.repository.ai_usage_snapshot(
                usage_date="2026-08-22", daily_limits={"llm": 1}
            )["llm"]["calls"],
            1,
        )
        self.assertEqual(
            self.repository.ai_usage_snapshot(
                usage_date="2026-08-23", daily_limits={"llm": 1}
            )["llm"]["calls"],
            1,
        )

    def test_llm_circuit_opens_without_another_client_call(self):
        guard = AIRequestGuard(
            self.repository,
            provider="llm",
            max_concurrent_requests=1,
            failure_threshold=1,
            cooldown_minutes=10,
        )
        client = FakeLLMClient(LLMTimeoutError("timeout"))
        parser = EpisodeParser(llm_backend=GuardedLLMParser(LLMParser(client), guard))
        first = parser.parse(description="原创短剧【归墟】九-终局")
        second = parser.parse(description="原创短剧【归墟】九-终局")
        self.assertEqual(first.reason, "llm_timeout")
        self.assertEqual(second.reason, "llm_circuit_open")
        self.assertEqual(client.calls, 1)
        self.assertEqual(guard.status()["status"], "cooldown")

    def test_ocr_circuit_is_independent(self):
        guard = AIRequestGuard(
            self.repository,
            provider="ocr",
            max_concurrent_requests=1,
            failure_threshold=1,
            cooldown_minutes=10,
        )
        backend = GuardedOCRBackend(FakeOCRBackend(error=RuntimeError("offline")), guard)
        with self.assertRaisesRegex(RuntimeError, "offline"):
            asyncio.run(backend.extract_text("https://example.invalid/a.jpg"))
        with self.assertRaisesRegex(AIRequestUnavailable, "ocr_circuit_open"):
            asyncio.run(backend.extract_text("https://example.invalid/a.jpg"))

    def test_provider_concurrency_limits_do_not_block_each_other(self):
        llm = AIRequestGuard(self.repository, provider="llm", max_concurrent_requests=1)
        ocr = AIRequestGuard(self.repository, provider="ocr", max_concurrent_requests=1)
        entered = threading.Event()
        release = threading.Event()

        def hold_llm():
            return llm.call(lambda: (entered.set(), release.wait(2), "done")[-1])

        worker = threading.Thread(target=hold_llm)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(ocr.call(lambda: "ocr-ok"), "ocr-ok")
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_v22_migration_creates_daily_usage_table(self):
        with self.repository._transaction() as connection:
            connection.execute("DROP TABLE ai_usage_daily")
            connection.execute("UPDATE app_meta SET value='22' WHERE key='schema_version'")
        migrated = ShortDramaRepository(self.repository.database_path)
        self.assertEqual(migrated.schema_version(), 24)
        self.assertTrue(migrated.reserve_ai_request(
            provider="llm", usage_date="2026-08-22", daily_call_limit=1
        ))


class AIGuardPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_budget_denial_keeps_scan_successful_and_enters_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ShortDramaRepository(Path(temp_dir) / "app.db")
            show = repository.create_show(title="归墟", normalized_title="归墟")
            guard = AIRequestGuard(
                repository, provider="llm", max_concurrent_requests=1, daily_call_limit=1
            )
            guard.call(lambda: "consume-budget")
            client = FakeLLMClient(llm_decision(show["id"]))
            parser = EpisodeParser(llm_backend=GuardedLLMParser(LLMParser(client), guard))
            homepage = "https://www.douyin.com/user/guard-sec"
            provider = FakeDouyinProvider(
                accounts_by_url={homepage: ProviderAccount(id="", sec_uid="guard-sec", homepage_url=homepage)},
                profiles_by_sec_uid={"guard-sec": ProviderProfile(nickname="短剧作者")},
                videos_by_sec_uid={"guard-sec": [ProviderVideo(
                    aweme_id="guard-1",
                    description="原创短剧【归墟】九-终局",
                    hashtags=(),
                    publish_time="2026-08-22T00:00:00+00:00",
                    video_url="https://www.douyin.com/video/guard-1",
                    cover_url=None,
                    raw={},
                )]},
            )
            pipeline = ShortDramaPipeline(repository=repository, provider=provider, parser=parser)
            account, _ = await pipeline.add_account(homepage)
            result = await pipeline.sync_account(account["id"])
            video = repository.get_video_by_aweme_id("guard-1")
            self.assertEqual(result.review_videos, 1)
            self.assertEqual(video["classification_status"], "review")
            self.assertEqual(video["parser_reason"], "llm_budget_exhausted")
            self.assertEqual(client.calls, 0)
