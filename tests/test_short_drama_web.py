from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class RecordingHistoryWorker:
    def __init__(self) -> None:
        self.wake_calls = 0

    def wake(self) -> None:
        self.wake_calls += 1

    def health_status(self) -> str:
        return "ok"


class ShortDramaWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")
        provider_account = ProviderAccount(id="", sec_uid="sec-1", homepage_url="https://www.douyin.com/user/sec-1")
        provider = FakeDouyinProvider(
            accounts_by_url={provider_account.homepage_url: provider_account},
            profiles_by_sec_uid={
                "sec-1": ProviderProfile(
                    nickname="AI剧场", avatar_url="https://img.example/ai-theater.jpg"
                )
            },
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
        history_worker = RecordingHistoryWorker()
        app = FastAPI()
        app.include_router(
            create_short_drama_router(
                repository=repository,
                pipeline=pipeline,
                history_backfill_worker=history_worker,
            )
        )
        self.client = TestClient(app)
        self.repository = repository
        self.provider = provider
        self.pipeline = pipeline
        self.history_worker = history_worker

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_dashboard_pages_and_show_api(self):
        response = self.client.get("/shows")
        self.assertEqual(response.status_code, 200)
        self.assertIn("全部短剧", response.text)
        self.assertIn("人工审核", response.text)
        scripts = [self.client.get(f"/static/{name}") for name in (
            "core.js", "shows.js", "library.js", "system.js", "app.js"
        )]
        self.assertTrue(all(item.status_code == 200 for item in scripts))
        static_source = "\n".join(item.text for item in scripts)
        page_source = response.text + static_source
        self.assertIn("startEditAccount", static_source)
        self.assertIn("batchIgnoreReviews", static_source)
        self.assertIn("startHistoryBackfill", static_source)
        self.assertIn("historyProgress", static_source)
        self.assertIn("history-progress-track", page_source)
        self.assertIn('status === "idle"', page_source)
        self.assertIn("尚未开始历史扫描", static_source)
        self.assertIn("抖音未提供历史总页数", static_source)
        self.assertIn("reparseAccount", static_source)
        self.assertIn("reparseVideo", static_source)
        self.assertIn("videoDescription", static_source)
        self.assertIn("parserEvidence", static_source)
        self.assertIn("解析证据", page_source)
        self.assertIn("reviewJudgements", page_source)
        self.assertIn("接受 AI 建议", page_source)
        self.assertIn('type="number" min="0"', page_source)
        self.assertIn("重新解析历史作品", page_source)
        self.assertIn("缺失集数", page_source)
        self.assertIn("合并短剧", page_source)
        self.assertIn("mergeShow(", page_source)
        self.assertIn("全部作者", page_source)
        self.assertIn("预计总集数", page_source)
        self.assertIn("永久忽略", page_source)
        self.assertIn("移除此集", page_source)
        self.assertIn("移除来源", page_source)
        self.assertIn("role=\"link\"", page_source)
        self.assertIn("!event.target.closest('.show-menu,.continue-link')", page_source)
        self.assertIn("mediaThumb", static_source)
        self.assertIn("account-avatar", page_source)
        self.assertIn("continue_watching", static_source)

        payload = self.client.get("/api/short-drama/shows").json()
        self.assertEqual(payload["shows"][0]["title"], "末日重生")
        self.assertEqual(payload["shows"][0]["continue_watching"]["episode_number"], 12)
        self.assertEqual(
            self.client.get("/api/short-drama/accounts").json()["accounts"][0]["avatar_url"],
            "https://img.example/ai-theater.jpg",
        )
        detail = self.client.get(f"/api/short-drama/shows/{payload['shows'][0]['id']}").json()
        self.assertEqual(detail["show"]["episodes"][0]["episode_number"], 12)
        self.assertEqual(detail["show"]["seasons"][0]["season_number"], 1)
        self.assertIn(1, detail["show"]["missing_episode_numbers"])
        videos = self.client.get("/api/short-drama/videos").json()["videos"]
        self.assertIn("content_type", videos[0])
        self.assertIn("show_title_candidate", videos[0])
        self.assertIn("episode_candidate", videos[0])
        self.assertIn("display_title", videos[0])
        self.assertIn("text_sources", videos[0])
        self.assertIn("parser_evidence", videos[0])

    async def test_show_library_management_endpoints(self):
        show = self.repository.list_show_summaries()[0]
        show_id = show["id"]
        episode = self.repository.get_show_episodes(show_id)[0]
        source = self.repository.get_episode_sources(episode["id"])[0]

        following_page = self.client.get("/following")
        self.assertEqual(following_page.status_code, 200)
        followed = self.client.post(f"/api/short-drama/shows/{show_id}/follow")
        self.assertEqual(followed.status_code, 200)
        self.assertTrue(followed.json()["show"]["is_following"])
        followed_items = self.client.get(
            "/api/short-drama/shows", params={"following": True}
        ).json()["shows"]
        self.assertEqual([item["id"] for item in followed_items], [show_id])
        unfollowed = self.client.post(f"/api/short-drama/shows/{show_id}/unfollow")
        self.assertFalse(unfollowed.json()["show"]["is_following"])

        updated = self.client.patch(
            f"/api/short-drama/shows/{show_id}",
            json={"expected_episode_count": 30, "aliases": ["末日归来"]},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["show"]["expected_episode_count"], 30)
        season_updated = self.client.patch(
            f"/api/short-drama/shows/{show_id}/seasons/1",
            json={"expected_episode_count": 24, "status": "completed"},
        )
        self.assertEqual(season_updated.status_code, 200)
        self.assertEqual(season_updated.json()["season"]["expected_episode_count"], 24)
        self.assertEqual(season_updated.json()["show"]["seasons"][0]["status"], "completed")
        searched = self.client.get("/api/short-drama/shows", params={"q": "末日归来"})
        self.assertEqual([item["id"] for item in searched.json()["shows"]], [show_id])

        ignored = self.client.post(
            f"/api/short-drama/shows/{show_id}/ignore",
            json={"reason": "误识别"},
        )
        self.assertEqual(ignored.status_code, 200)
        self.assertTrue(ignored.json()["show"]["is_ignored"])
        self.assertEqual(self.client.get("/api/short-drama/shows").json()["shows"], [])
        ignored_items = self.client.get(
            "/api/short-drama/shows", params={"ignored": "ignored"}
        ).json()["shows"]
        self.assertEqual(ignored_items[0]["id"], show_id)
        restored = self.client.post(f"/api/short-drama/shows/{show_id}/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(restored.json()["show"]["is_ignored"])

        cleared = self.client.patch(
            f"/api/short-drama/shows/{show_id}",
            json={"expected_episode_count": None},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["show"]["expected_episode_count"])

        removed = self.client.delete(
            f"/api/short-drama/shows/{show_id}/episodes/{episode['id']}"
            f"/sources/{source['id']}"
        )
        self.assertEqual(removed.status_code, 200)
        self.assertTrue(removed.json()["result"]["episode_removed"])
        stored_video = self.repository.get_video(source["video_id"])
        self.assertEqual(stored_video["parser_reason"], "manual_remove_source")
        self.assertEqual(self.repository.get_show_episodes(show_id), [])
        self.assertEqual(self.client.get("/api/short-drama/shows").json()["shows"], [])
        empty = self.client.get(
            "/api/short-drama/shows", params={"include_empty": True}
        ).json()["shows"]
        self.assertEqual(empty[0]["id"], show_id)

    async def test_update_feed_endpoints_and_page(self):
        show = self.repository.list_show_summaries()[0]
        account = self.repository.list_accounts()[0]
        update_video = self.repository.create_video(
            aweme_id="web-update", account_id=account["id"], description="第13集",
            hashtags=[], publish_time="2026-08-16T00:00:00+00:00",
            video_url="https://www.douyin.com/video/web-update", cover_url=None, raw={},
        )[0]
        self.repository.record_episode_source(
            show_id=show["id"], episode_number=13, video_id=update_video["id"],
            account_id=account["id"], published_at=update_video["publish_time"],
            create_update_event=True,
        )

        self.assertEqual(self.client.get("/updates").status_code, 200)
        feed = self.client.get(
            "/api/short-drama/updates", params={"following_only": False}
        ).json()
        self.assertEqual(feed["total"], 1)
        self.assertEqual(feed["groups"][0]["count"], 1)
        event_id = feed["events"][0]["id"]
        self.assertIsNotNone(
            self.client.post(f"/api/short-drama/updates/{event_id}/read")
            .json()["event"]["read_at"]
        )
        self.assertEqual(
            self.client.post("/api/short-drama/updates/read-all").json()["marked_read"], 0
        )

    async def test_watch_progress_endpoint_is_independent_from_update_read(self):
        show = self.repository.list_show_summaries()[0]
        response = self.client.put(
            f"/api/short-drama/shows/{show['id']}/seasons/1/watch-progress",
            json={"watched_episode_number": 10},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress"]["watched_episode_number"], 10)
        self.assertEqual(response.json()["show"]["seasons"][0]["watched_episode_number"], 10)
        self.assertEqual(
            self.client.get(
                f"/api/short-drama/shows/{show['id']}/seasons/1/watch-progress"
            ).json()["progress"]["watched_episode_number"],
            10,
        )

    async def test_show_merge_and_consistency_repair_endpoints(self):
        target = self.client.get("/api/short-drama/shows").json()["shows"][0]
        account = self.repository.list_accounts()[0]
        source = self.repository.create_show(title="末日重生 I", normalized_title="末日重生i")
        video, created = self.repository.create_video(
            aweme_id="merge-web-1",
            account_id=account["id"],
            description="《末日重生 I》第1集",
            hashtags=[],
            publish_time="2026-08-01T00:00:00+00:00",
            video_url="https://www.douyin.com/video/merge-web-1",
            cover_url=None,
            raw={},
        )
        self.assertTrue(created)
        self.repository.record_episode_source(
            show_id=source["id"],
            episode_number=1,
            video_id=video["id"],
            account_id=account["id"],
            published_at=video["publish_time"],
        )

        merged = self.client.post(
            f"/api/short-drama/shows/{target['id']}/merge",
            json={"source_show_id": source["id"]},
        )
        repaired = self.client.post("/api/short-drama/repair-consistency")

        self.assertEqual(merged.status_code, 200)
        self.assertEqual(merged.json()["show"]["id"], target["id"])
        self.assertIsNone(self.repository.get_show(source["id"]))
        self.assertEqual(repaired.status_code, 200)
        self.assertIn("episodes_repaired", repaired.json()["result"])
        openapi = self.client.get("/openapi.json").json()
        self.assertIn("/api/short-drama/shows/{target_show_id}/merge", openapi["paths"])
        self.assertIn("/api/short-drama/repair-consistency", openapi["paths"])

    async def test_account_endpoint_updates_editable_fields(self):
        account = self.repository.list_accounts()[0]

        response = self.client.patch(
            f"/api/short-drama/accounts/{account['id']}",
            json={
                "nickname": "更新后的 AI 剧场",
                "homepage_url": "https://www.douyin.com/user/updated-sec-1",
                "check_interval_minutes": 15,
                "schedule_mode": "adaptive",
                "adaptive_min_interval_minutes": 20,
                "adaptive_max_interval_minutes": 180,
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.json()["account"]
        self.assertEqual(updated["nickname"], "更新后的 AI 剧场")
        self.assertEqual(updated["homepage_url"], "https://www.douyin.com/user/updated-sec-1")
        self.assertEqual(updated["check_interval_minutes"], 15)
        self.assertEqual(updated["schedule_mode"], "adaptive")
        self.assertEqual(updated["adaptive_min_interval_minutes"], 20)
        self.assertEqual(updated["adaptive_max_interval_minutes"], 180)
        cleared = self.client.patch(
            f"/api/short-drama/accounts/{account['id']}",
            json={
                "adaptive_min_interval_minutes": None,
                "adaptive_max_interval_minutes": None,
            },
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["account"]["adaptive_min_interval_minutes"])

    async def test_accounts_ui_exposes_per_account_scheduling_controls(self):
        script = self.client.get("/static/library.js")
        self.assertEqual(script.status_code, 200)
        for text in ("调度方式", "跟随系统", "自适应最短间隔", "实际间隔"):
            self.assertIn(text, script.text)

    async def test_optional_admin_token_protects_writes_but_not_reads_or_health(self):
        protected_app = FastAPI()
        protected_app.include_router(
            create_short_drama_router(
                repository=self.repository,
                pipeline=self.pipeline,
                admin_api_token="phase-two-secret",
            )
        )
        client = TestClient(protected_app)
        account = self.repository.list_accounts()[0]

        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/api/short-drama/accounts").status_code, 200)
        denied = client.patch(
            f"/api/short-drama/accounts/{account['id']}",
            json={"nickname": "不应保存"},
        )
        allowed = client.patch(
            f"/api/short-drama/accounts/{account['id']}",
            headers={"Authorization": "Bearer phase-two-secret"},
            json={"nickname": "已授权"},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["account"]["nickname"], "已授权")

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

    async def test_review_endpoint_accepts_episode_zero_and_rejects_negative(self):
        account = self.repository.list_accounts()[0]
        show = self.repository.list_shows()[0]
        video, _ = self.repository.create_video(
            aweme_id="review-zero",
            account_id=account["id"],
            description="《末日重生》第0集",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/review-zero",
            cover_url=None,
            raw={},
        )
        self.repository.update_video_processing(
            video["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.7,
            classification_status="review",
            parser_reason="manual_zero_review",
        )

        accepted = self.client.post(
            f"/api/short-drama/reviews/{video['id']}",
            json={"show_id": show["id"], "episode_number": 0},
        )
        negative = self.client.post(
            f"/api/short-drama/reviews/{video['id']}",
            json={"show_id": show["id"], "episode_number": -1},
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["episode"]["episode_number"], 0)
        self.assertEqual(negative.status_code, 422)

    async def test_review_accepts_second_season_episode_zero(self):
        account = self.repository.list_accounts()[0]
        show = self.repository.list_shows()[0]
        video, _ = self.repository.create_video(
            aweme_id="review-season-two-zero",
            account_id=account["id"],
            description="《末日重生》第二季先导片",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/review-season-two-zero",
            cover_url=None,
            raw={},
        )
        self.repository.update_video_processing(
            video["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.7,
            classification_status="review",
            parser_reason="trailer_requires_review",
            parsed_season_number=2,
            season_candidate=2,
        )
        accepted = self.client.post(
            f"/api/short-drama/reviews/{video['id']}",
            json={"show_id": show["id"], "season_number": 2, "episode_number": 0},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["episode"]["season_number"], 2)
        self.assertEqual(accepted.json()["episode"]["episode_number"], 0)

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
        self.assertEqual(resumed.json()["account"]["history_sync_status"], "pending")
        self.assertEqual(self.history_worker.wake_calls, 2)
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


    async def test_advanced_correction_and_batch_video_endpoints(self):
        show = self.repository.list_shows()[0]
        episode = self.repository.get_show_episodes(show["id"])[0]
        moved = self.client.post(
            f"/api/short-drama/episodes/{episode['id']}/move",
            json={"target_show_id": show["id"], "season_number": 2, "episode_number": 1},
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["result"]["show"]["latest_season"], 2)
        corrections = self.client.get("/api/short-drama/corrections").json()["corrections"]
        self.assertEqual(corrections[0]["operation_type"], "move_episode")
        video_id = self.repository.list_videos()[0]["id"]
        ignored = self.client.post("/api/short-drama/videos/batch-ignore", json={"video_ids": [video_id]})
        self.assertEqual(ignored.status_code, 200)
        self.assertEqual(ignored.json()["ignored_count"], 1)
        reparsed = self.client.post("/api/short-drama/videos/batch-reparse", json={"video_ids": [video_id]})
        self.assertEqual(reparsed.status_code, 200)
        self.assertEqual(reparsed.json()["reparsed_count"], 1)

    async def test_diagnostics_are_redacted_and_doctor_is_read_only(self):
        with patch("douyin_user_monitor.web.short_drama.doctor_database") as doctor:
            data = self.client.get("/api/short-drama/diagnostics").json()
        doctor.assert_not_called()
        self.assertEqual(data["database"]["schema_version"], 22)
        self.assertIsNone(data["database"]["last_doctor_at"])
        self.assertGreaterEqual(data["database"]["database_latency_ms"], 0)
        self.assertEqual(
            set(data["queues"]),
            {"notification_queue", "review_queue", "history_queue"},
        )
        self.assertEqual(
            set(data["workers"]),
            {"scheduler", "history", "notification", "maintenance"},
        )
        self.assertIn("llm_calls", data["parser_metrics_24h"])
        self.assertNotIn("token", str(data).lower())
        with patch("douyin_user_monitor.web.short_drama.doctor_database") as doctor:
            doctor.return_value.ok = True
            doctor.return_value.checks = {"integrity": "ok"}
            self.assertTrue(self.client.post("/api/short-drama/diagnostics/doctor").json()["ok"])
            doctor.assert_called_once_with(self.repository.database_path)
        restarted = ShortDramaRepository(self.repository.database_path)
        persisted = restarted.get_service_state(
            "last_doctor_at", "last_doctor_ok", "last_doctor_summary"
        )
        self.assertIsNotNone(persisted["last_doctor_at"])
        self.assertTrue(persisted["last_doctor_ok"])
        self.assertEqual(persisted["last_doctor_summary"], {"integrity": "ok"})

    async def test_quality_page_and_api_expose_all_categories(self):
        self.assertEqual(self.client.get("/quality").status_code, 200)
        data = self.client.get("/api/short-drama/quality").json()
        self.assertEqual(set(data["categories"]), {"review", "missing_episodes", "suspicious_jumps",
            "expected_count_conflicts", "source_less_episodes", "low_confidence", "ocr_only",
            "outdated_parser", "stale_shows"})

    async def test_pwa_metadata_assets_and_mobile_navigation(self):
        page = self.client.get("/following")
        manifest = self.client.get("/manifest.webmanifest")
        worker = self.client.get("/sw.js")
        static_js = self.client.get("/static/app.js")
        self.assertIn('rel="manifest"', page.text)
        self.assertIn("bottom-nav", page.text)
        self.assertIn("serviceWorker.register", static_js.text)
        for asset in ("core.js", "shows.js", "library.js", "system.js"):
            response = self.client.get(f"/static/{asset}")
            self.assertEqual(response.status_code, 200)
            self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(manifest.json()["start_url"], "/following")
        self.assertIn('url.pathname.startsWith("/api/")', worker.text)
        self.assertNotIn("caches.match(event.request)", worker.text.split('url.pathname.startsWith("/api/")', 1)[1].split("return;", 1)[0])

    async def test_build_identity_versions_assets_and_service_worker_cache(self):
        page = self.client.get("/following")
        version = self.client.get("/version")
        self.assertEqual(version.status_code, 200)
        build_id = version.json()["build_id"]
        self.assertRegex(build_id, r"^[0-9a-f]{16}$")
        self.assertIn(f"/static/app.js?v={build_id}", page.text)
        versioned = self.client.get(f"/static/app.js?v={build_id}")
        unversioned = self.client.get("/static/app.js")
        self.assertIn("immutable", versioned.headers["cache-control"])
        self.assertEqual(unversioned.headers["cache-control"], "no-cache")
        worker = self.client.get("/sw.js")
        self.assertIn(f'short-drama-shell-${{BUILD_ID}}', worker.text)
        self.assertIn(f'const BUILD_ID = "{build_id}"', worker.text)


if __name__ == "__main__":
    unittest.main()
