from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile, ProviderVideo
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

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_dashboard_pages_and_show_api(self):
        response = self.client.get("/shows")
        self.assertEqual(response.status_code, 200)
        self.assertIn("最近更新短剧", response.text)
        self.assertIn("人工审核", response.text)
        self.assertIn("startEditAccount", response.text)
        self.assertIn("batchIgnoreReviews", response.text)

        payload = self.client.get("/api/short-drama/shows").json()
        self.assertEqual(payload["shows"][0]["title"], "末日重生")
        detail = self.client.get(f"/api/short-drama/shows/{payload['shows'][0]['id']}").json()
        self.assertEqual(detail["show"]["episodes"][0]["episode_number"], 12)

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


if __name__ == "__main__":
    unittest.main()
