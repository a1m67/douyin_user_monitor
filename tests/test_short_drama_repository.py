from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository


class ShortDramaRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repository = ShortDramaRepository(self.root / "app.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_account(self, sec_uid: str = "sec-1") -> dict:
        return self.repository.create_account(
            sec_uid=sec_uid,
            nickname=f"作者-{sec_uid}",
            homepage_url=f"https://www.douyin.com/user/{sec_uid}",
        )

    def create_video(self, account_id: str, aweme_id: str) -> dict:
        video, created = self.repository.create_video(
            aweme_id=aweme_id,
            account_id=account_id,
            description="《末日重生》第27集",
            hashtags=["末日重生"],
            publish_time="2026-08-15T12:31:00+00:00",
            video_url=f"https://www.douyin.com/video/{aweme_id}",
            cover_url=None,
            raw={"aweme_id": aweme_id},
        )
        self.assertTrue(created)
        return video

    def test_aweme_id_is_database_unique_and_duplicate_is_not_created_twice(self):
        account = self.create_account()
        first = self.create_video(account["id"], "1001")
        second, created = self.repository.create_video(
            aweme_id="1001",
            account_id=account["id"],
            description="ignored duplicate",
            hashtags=[],
            publish_time=None,
            video_url="",
            cover_url=None,
            raw={},
        )

        self.assertFalse(created)
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(self.repository.counts()["videos"], 1)

    def test_same_show_and_episode_keeps_multiple_sources_but_one_episode(self):
        first_account = self.create_account("sec-1")
        second_account = self.create_account("sec-2")
        show = self.repository.create_show(
            title="末日重生",
            normalized_title="末日重生",
            aliases=["重生末日"],
        )
        first_video = self.create_video(first_account["id"], "1001")
        first_result = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=27,
            video_id=first_video["id"],
            account_id=first_account["id"],
            published_at=first_video["publish_time"],
        )
        second_video = self.create_video(second_account["id"], "2001")
        second_result = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=27,
            video_id=second_video["id"],
            account_id=second_account["id"],
            published_at=second_video["publish_time"],
        )

        self.assertTrue(first_result.is_new_episode)
        self.assertFalse(second_result.is_new_episode)
        self.assertEqual(len(self.repository.get_show_episodes(show["id"])), 1)
        self.assertEqual(len(self.repository.get_episode_sources(first_result.episode["id"])), 2)
        self.assertEqual(self.repository.get_show(show["id"])["latest_episode"], 27)

    def test_legacy_json_accounts_and_aweme_ids_are_imported_as_processed_baseline(self):
        legacy_path = self.root / "monitor_users.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "users": [
                        {
                            "id": "legacy-account",
                            "sec_user_id": "legacy-sec",
                            "profile_url": "https://www.douyin.com/user/legacy",
                            "nickname": "旧账号",
                            "downloaded_aweme_ids": ["old-1"],
                            "download_records": [
                                {
                                    "aweme_id": "old-1",
                                    "desc": "旧视频",
                                    "downloaded_at": "2026-08-15T10:00:00+00:00",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        imported = ShortDramaRepository(self.root / "legacy.db", legacy_state_path=legacy_path)

        account = imported.get_account_by_sec_uid("legacy-sec")
        video = imported.get_video_by_aweme_id("old-1")
        self.assertIsNotNone(account)
        self.assertTrue(account["initial_sync_completed"])
        self.assertIsNotNone(video)
        self.assertTrue(video["is_processed"])
        self.assertFalse(video["needs_review"])


if __name__ == "__main__":
    unittest.main()
