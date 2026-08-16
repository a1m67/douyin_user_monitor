from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from douyin_user_monitor.providers.base import (
    ProviderAccount,
    ProviderProfile,
    ProviderVideo,
    ProviderVideoPage,
)
from douyin_user_monitor.providers.fake import FakeDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.web.short_drama import create_short_drama_router


class ShortDramaWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")
        provider_account = ProviderAccount(id="", sec_uid="sec-1", homepage_url="https://www.douyin.com/user/sec-1")
        provider = FakeDouyinProvider(
            accounts_by_url={provider_account.homepage_url: provider_account},
            profiles_by_sec_uid={"sec-1": ProviderProfile(nickname="AI剧场")},
            videos_by_sec_uid={
                "sec-1": [
                    ProviderVideo(
                        aweme_id="1001",
                        description="《末日重生》第12集",
                        hashtags=("末日重生",),
                        publish_time="2026-08-15T12:31:00+00:00",
                        video_url="https://www.douyin.com/video/1001",
                        cover_url=None,
                        raw={"aweme_id": "1001"},
                    )
                ]
            },
        )
        pipeline = ShortDramaPipeline(repository=repository, provider=provider)
        account, _ = await pipeline.add_account(provider_account.homepage_url)
        await pipeline.sync_account(account["id"])
        app = FastAPI()
        app.include_router(create_short_drama_router(repository=repository, pipeline=pipeline))
        self.client = TestClient(app)
        self.repository = repository
        self.provider = provider
        self.pipeline = pipeline

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_dashboard_pages_and_show_api(self):
        response = self.client.get("/shows")
        self.assertEqual(response.status_code, 200)
        self.assertIn("最近更新短剧", response.text)
        self.assertIn("人工审核", response.text)
        self.assertIn("startEditAccount", response.text)
        self.assertIn("batchIgnoreReviews", response.text)
        self.assertIn("startHistoryBackfill", response.text)
        self.assertIn("historyProgress", response.text)
        self.assertIn("history-progress-track", response.text)
        self.assertIn('status === "idle"', response.text)
        self.assertIn("尚未开始历史扫描", response.text)
        self.assertIn("抖音未提供历史总页数", response.text)
        self.assertIn("reparseAccount", response.text)
        self.assertIn("reparseVideo", response.text)
        self.assertIn("重新解析历史作品", response.text)
        self.assertIn("缺失集数", response.text)

        payload = self.client.get("/api/short-drama/shows").json()
        self.assertEqual(payload["shows"][0]["title"], "末日重生")
        detail = self.client.get(f"/api/short-drama/shows/{payload['shows'][0]['id']}").json()
        self.assertEqual(detail["show"]["episodes"][0]["episode_number"], 12)
        self.assertIn(1, detail["show"]["missing_episode_numbers"])
        videos = self.client.get("/api/short-drama/videos").json()["videos"]
        self.assertIn("content_type", videos[0])
        self.assertIn("show_title_candidate", videos[0])
        self.assertIn("episode_candidate", videos[0])

    async def test_account_endpoint_updates_editable_fields(self):
        account = self.repository.list_accounts()[0]

        response = self.client.patch(
            f"/api/short-drama/accounts/{account['id']}",
            json={
                "nickname": "更新后的 AI 剧场",
                "homepage_url": "https://www.douyin.com/user/updated-sec-1",
                "check_interval_minutes": 15,
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.json()["account"]
        self.assertEqual(updated["nickname"], "更新后的 AI 剧场")
        self.assertEqual(updated["homepage_url"], "https://www.douyin.com/user/updated-sec-1")
        self.assertEqual(updated["check_interval_minutes"], 15)

    async def test_review_endpoint_confirms_a_video(self):
        account = self.repository.list_accounts()[0]
        video, _ = self.repository.create_video(
            aweme_id="review-1",
            account_id=account["id"],
            description="第13集",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/review-1",
            cover_url=None,
            raw={},
        )
        self.repository.update_video_processing(
            video["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.4,
            parsed_episode_number=13,
            parser_method="regex:episode_without_title",
        )
        response = self.client.post(
            f"/api/short-drama/reviews/{video['id']}",
            json={"new_show_title": "新剧", "episode_number": 13},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["new_episode"])
        self.assertFalse(self.repository.get_video(video["id"])["needs_review"])
        self.assertEqual(self.repository.get_video(video["id"])["classification_status"], "matched")

    async def test_review_ignore_endpoints_only_return_real_review_videos(self):
        account = self.repository.list_accounts()[0]
        review_videos = []
        for aweme_id in ("review-ignore-1", "review-ignore-2"):
            video, _ = self.repository.create_video(
                aweme_id=aweme_id,
                account_id=account["id"],
                description="第13集",
                hashtags=[],
                publish_time=None,
                video_url=f"https://www.douyin.com/video/{aweme_id}",
                cover_url=None,
                raw={},
            )
            self.repository.update_video_processing(
                video["id"],
                is_processed=False,
                needs_review=True,
                parser_confidence=0.4,
                parsed_episode_number=13,
                parser_method="regex:episode_without_title",
                parser_reason="episode_signal_without_reliable_title",
                classification_status="review",
            )
            review_videos.append(video)

        visible = self.client.get("/api/short-drama/videos?classification_status=review")
        self.assertEqual(visible.status_code, 200)
        self.assertEqual({video["id"] for video in visible.json()["videos"]}, {video["id"] for video in review_videos})

        ignored = self.client.post(f"/api/short-drama/reviews/{review_videos[0]['id']}/ignore")
        self.assertEqual(ignored.status_code, 200)
        self.assertEqual(ignored.json()["video"]["classification_status"], "ignored")

        batch = self.client.post(
            "/api/short-drama/reviews/batch-ignore",
            json={"video_ids": [review_videos[1]["id"]]},
        )
        self.assertEqual(batch.status_code, 200)
        self.assertEqual(batch.json()["ignored_count"], 1)
        self.assertEqual(
            self.client.get("/api/short-drama/videos?classification_status=review").json()["videos"],
            [],
        )

    async def test_history_backfill_endpoints_control_one_page_at_a_time(self):
        account = self.repository.list_accounts()[0]
        self.provider.video_pages_by_sec_uid["sec-1"] = {
            0: ProviderVideoPage(
                videos=(
                    ProviderVideo(
                        aweme_id="history-1002",
                        description="《末日重生》第13集",
                        hashtags=("末日重生",),
                        publish_time="2026-08-15T12:32:00+00:00",
                        video_url="https://www.douyin.com/video/history-1002",
                        cover_url=None,
                        raw={"aweme_id": "history-1002"},
                    ),
                ),
                next_cursor=50,
                has_more=False,
            )
        }

        started = self.client.post(f"/api/short-drama/accounts/{account['id']}/history/start")
        paused = self.client.post(f"/api/short-drama/accounts/{account['id']}/history/pause")
        resumed = self.client.post(f"/api/short-drama/accounts/{account['id']}/history/resume")
        page = self.client.post(f"/api/short-drama/accounts/{account['id']}/history/next-page")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["account"]["history_sync_status"], "pending")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["account"]["history_sync_status"], "paused")
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["result"]["new_videos"], 1)
        self.assertEqual(page.json()["result"]["history_sync"]["status"], "completed")

    async def test_reparse_video_endpoint_reuses_current_parser_without_notification(self):
        account = self.repository.list_accounts()[0]
        video, _ = self.repository.create_video(
            aweme_id="legacy-reparse-1",
            account_id=account["id"],
            description="《末日重生》第13集",
            hashtags=("末日重生",),
            publish_time="2026-08-15T12:32:00+00:00",
            video_url="https://www.douyin.com/video/legacy-reparse-1",
            cover_url=None,
            raw={"aweme_id": "legacy-reparse-1"},
        )
        self.repository.update_video_processing(
            video["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=None,
            classification_status="ignored",
            parser_reason="legacy_ignored",
        )

        response = self.client.post(f"/api/short-drama/videos/{video['id']}/reparse")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "matched")
        self.assertTrue(response.json()["new_episode"])
        openapi = self.client.get("/openapi.json").json()
        self.assertIn("/api/short-drama/videos/{video_id}/reparse", openapi["paths"])
        stored = self.repository.get_video(video["id"])
        self.assertEqual(stored["classification_status"], "matched")
        self.assertEqual(stored["parser_method"], "regex:bracketed_known")

    async def test_account_reparse_endpoint_defaults_to_legacy_ignored_scope(self):
        account = self.repository.list_accounts()[0]
        video, _ = self.repository.create_video(
            aweme_id="legacy-reparse-account-1",
            account_id=account["id"],
            description="《末日重生》第13集",
            hashtags=("末日重生",),
            publish_time="2026-08-15T12:32:00+00:00",
            video_url="https://www.douyin.com/video/legacy-reparse-account-1",
            cover_url=None,
            raw={"aweme_id": "legacy-reparse-account-1"},
        )
        self.repository.update_video_processing(
            video["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=None,
            classification_status="ignored",
            parser_reason="legacy_ignored",
        )

        response = self.client.post(f"/api/short-drama/accounts/{account['id']}/reparse")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["requested_videos"], 1)
        self.assertEqual(response.json()["result"]["matched_videos"], 1)


if __name__ == "__main__":
    unittest.main()
