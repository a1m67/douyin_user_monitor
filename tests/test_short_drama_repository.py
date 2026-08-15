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

    def test_deleting_account_reassigns_shared_episode_and_removes_orphaned_episodes(self):
        first_account = self.create_account("sec-1")
        second_account = self.create_account("sec-2")
        show = self.repository.create_show(
            title="末日重生",
            normalized_title="末日重生",
        )
        first_video = self.create_video(first_account["id"], "1001")
        shared_episode = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=27,
            video_id=first_video["id"],
            account_id=first_account["id"],
            published_at=first_video["publish_time"],
        )
        second_video = self.create_video(second_account["id"], "2001")
        self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=27,
            video_id=second_video["id"],
            account_id=second_account["id"],
            published_at=second_video["publish_time"],
        )
        orphaned_video = self.create_video(first_account["id"], "1002")
        self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=28,
            video_id=orphaned_video["id"],
            account_id=first_account["id"],
            published_at=orphaned_video["publish_time"],
        )

        deleted = self.repository.delete_account(first_account["id"])

        self.assertEqual(deleted["id"], first_account["id"])
        self.assertIsNone(self.repository.get_account(first_account["id"]))
        self.assertEqual(self.repository.counts()["videos"], 1)
        episodes = self.repository.get_show_episodes(show["id"])
        self.assertEqual([episode["episode_number"] for episode in episodes], [27])
        self.assertEqual(episodes[0]["id"], shared_episode.episode["id"])
        self.assertEqual(episodes[0]["first_video_id"], second_video["id"])
        self.assertEqual(episodes[0]["first_account_id"], second_account["id"])
        self.assertEqual(
            [source["video_id"] for source in self.repository.get_episode_sources(episodes[0]["id"])],
            [second_video["id"]],
        )
        refreshed_show = self.repository.get_show(show["id"])
        self.assertEqual(refreshed_show["latest_episode"], 27)
        self.assertEqual(refreshed_show["latest_update_at"], second_video["publish_time"])

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
        self.assertEqual(video["classification_status"], "ignored")

    def test_schema_upgrade_maps_legacy_review_and_repairs_placeholder_nickname(self):
        account = self.create_account("legacy-sec")
        video = self.create_video(account["id"], "legacy-review")
        self.repository.update_video_processing(
            video["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.4,
            parsed_episode_number=12,
            parser_method="regex:episode_without_title",
        )
        with self.repository._transaction() as connection:
            connection.execute("UPDATE accounts SET nickname = 'nan' WHERE id = ?", (account["id"],))
            connection.execute(
                "UPDATE videos SET classification_status = 'ignored', parser_reason = NULL WHERE id = ?",
                (video["id"],),
            )
            connection.execute(
                "UPDATE app_meta SET value = '1' WHERE key = 'schema_version'",
            )

        upgraded = ShortDramaRepository(self.root / "app.db")
        upgraded_video = upgraded.get_video(video["id"])
        upgraded_account = upgraded.get_account(account["id"])

        self.assertEqual(upgraded_video["classification_status"], "review")
        self.assertEqual(upgraded_video["parser_reason"], "legacy_review")
        self.assertTrue(upgraded_video["needs_review"])
        self.assertEqual(upgraded_account["nickname"], "作者 legacy-sec")

    def test_batch_ignore_only_changes_review_videos(self):
        account = self.create_account()
        review_video = self.create_video(account["id"], "review-1")
        ignored_video = self.create_video(account["id"], "ignored-1")
        self.repository.update_video_processing(
            review_video["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.4,
            parsed_episode_number=12,
            parser_method="regex:episode_without_title",
        )
        self.repository.update_video_processing(
            ignored_video["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=0.0,
            parser_method="regex:no_short_drama_signal",
        )

        ignored_count = self.repository.ignore_review_videos([review_video["id"], ignored_video["id"]])

        self.assertEqual(ignored_count, 1)
        self.assertEqual(self.repository.get_video(review_video["id"])["classification_status"], "ignored")
        self.assertEqual(self.repository.get_video(ignored_video["id"])["classification_status"], "ignored")


if __name__ == "__main__":
    unittest.main()
