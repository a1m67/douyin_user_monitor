from __future__ import annotations

import json
import sqlite3
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

    def create_video_at(self, account_id: str, aweme_id: str, published_at: str) -> dict:
        video, created = self.repository.create_video(
            aweme_id=aweme_id,
            account_id=account_id,
            description="《末日重生》第27集",
            hashtags=["末日重生"],
            publish_time=published_at,
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

    def test_video_text_metadata_and_parser_evidence_round_trip(self):
        account = self.create_account()
        video, created = self.repository.create_video(
            aweme_id="metadata-1",
            account_id=account["id"],
            description="原创ai漫剧《契鬼人》义庄副本第一夜",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/metadata-1",
            cover_url=None,
            raw={"aweme_id": "metadata-1"},
            display_title="第8集 | 原创ai漫剧《契鬼人》义庄副本第一夜",
            text_sources={
                "series_play_info.item_title_prefix.text": "第8集",
                "item_title": "原创ai漫剧《契鬼人》义庄副本第一夜",
            },
        )
        self.assertTrue(created)
        updated = self.repository.update_video_processing(
            video["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=0.97,
            parsed_show_title="契鬼人",
            parsed_episode_number=8,
            parser_method="regex:bracketed",
            classification_status="matched",
            parser_reason="explicit_bracketed_title_and_episode",
            show_title_candidate="契鬼人",
            episode_candidate=8,
            content_type="episode",
            parser_evidence={
                "episode": {"source_field": "series_play_info.item_title_prefix.text", "value": 8},
                "show": {"source_field": "item_title", "value": "契鬼人"},
            },
        )

        self.assertEqual(updated["display_title"], "第8集 | 原创ai漫剧《契鬼人》义庄副本第一夜")
        self.assertEqual(updated["text_sources"]["series_play_info.item_title_prefix.text"], "第8集")
        self.assertEqual(updated["parser_evidence"]["episode"]["value"], 8)
        self.assertEqual(updated["parser_evidence"]["show"]["source_field"], "item_title")

    def test_refresh_video_metadata_merges_richer_values_and_preserves_existing_data(self):
        account = self.create_account()
        video, created = self.repository.create_video(
            aweme_id="metadata-refresh-1",
            account_id=account["id"],
            description="完整的旧描述",
            hashtags=[],
            publish_time=None,
            video_url="https://www.douyin.com/video/metadata-refresh-1",
            cover_url="https://cover.example/old.jpg",
            raw={"aweme_id": "metadata-refresh-1", "kept": "value"},
            display_title="旧标题",
            text_sources={"item_title": "旧标题"},
        )
        self.assertTrue(created)

        unchanged, changed = self.repository.refresh_video_metadata(
            video["id"],
            description="",
            video_url="",
            cover_url=None,
            raw={},
            display_title=None,
            text_sources={},
        )
        self.assertFalse(changed)
        self.assertEqual(unchanged["description"], "完整的旧描述")
        self.assertEqual(unchanged["display_title"], "旧标题")
        self.assertEqual(unchanged["video_url"], "https://www.douyin.com/video/metadata-refresh-1")
        self.assertEqual(unchanged["cover_url"], "https://cover.example/old.jpg")

        refreshed, changed = self.repository.refresh_video_metadata(
            video["id"],
            description="完整的旧描述，并补充了来自最新作品列表的更多内容",
            video_url="https://www.douyin.com/video/metadata-refresh-1?source=latest",
            cover_url="https://cover.example/new.jpg",
            raw={"aweme_id": "metadata-refresh-1", "new_field": "new value"},
            display_title="第8集 | 旧标题",
            text_sources={
                "series_play_info.item_title_prefix.text": "第8集",
                "item_title": "旧标题",
            },
        )
        self.assertTrue(changed)
        raw = json.loads(refreshed["raw_json"])
        self.assertEqual(raw["kept"], "value")
        self.assertEqual(raw["new_field"], "new value")
        self.assertEqual(refreshed["display_title"], "第8集 | 旧标题")
        self.assertEqual(
            refreshed["text_sources"]["series_play_info.item_title_prefix.text"],
            "第8集",
        )
        self.assertEqual(refreshed["description"], "完整的旧描述，并补充了来自最新作品列表的更多内容")
        self.assertEqual(
            refreshed["video_url"],
            "https://www.douyin.com/video/metadata-refresh-1?source=latest",
        )
        self.assertEqual(refreshed["cover_url"], "https://cover.example/old.jpg")

    def test_system_status_includes_accounts_with_sync_errors(self):
        account = self.create_account()
        self.repository.mark_account_sync_failure(
            account["id"],
            error="temporary Douyin response failure",
            next_check_at="2026-08-16T10:00:00+00:00",
        )

        status = self.repository.system_status()

        self.assertEqual(status["accounts"], 1)
        self.assertEqual(len(status["recent_errors"]), 1)
        self.assertEqual(status["recent_errors"][0]["id"], account["id"])
        self.assertEqual(status["recent_errors"][0]["last_error"], "temporary Douyin response failure")

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

    def test_same_episode_number_in_two_seasons_creates_two_episodes(self):
        account = self.create_account("season-account")
        show = self.repository.create_show(title="归墟", normalized_title="归墟")
        first = self.create_video(account["id"], "season-1-episode-1")
        second = self.create_video(account["id"], "season-2-episode-1")
        for season, video in ((1, first), (2, second)):
            self.repository.record_episode_source(
                show_id=show["id"], season_number=season, episode_number=1,
                video_id=video["id"], account_id=account["id"],
                published_at=video["publish_time"],
            )

        episodes = self.repository.get_show_episodes(show["id"])
        self.assertEqual(
            [(item["season_number"], item["episode_number"]) for item in episodes],
            [(2, 1), (1, 1)],
        )
        detail = self.repository.get_show_detail(show["id"])
        self.assertEqual(detail["latest_season"], 2)

    def test_v8_episode_migration_preserves_sources_and_defaults_season_one(self):
        account = self.create_account("migration-season")
        show = self.repository.create_show(title="旧剧", normalized_title="旧剧")
        video = self.create_video(account["id"], "migration-season-video")
        write = self.repository.record_episode_source(
            show_id=show["id"], episode_number=7, video_id=video["id"],
            account_id=account["id"], published_at=video["publish_time"],
        )
        database_path = self.root / "app.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = OFF;
                PRAGMA legacy_alter_table = ON;
                ALTER TABLE episodes RENAME TO episodes_v9_source;
                ALTER TABLE episode_sources RENAME TO episode_sources_v9_source;
                CREATE TABLE episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                    episode_number INTEGER NOT NULL,
                    first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
                    first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    published_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(show_id, episode_number),
                    CHECK (episode_number >= 0)
                );
                INSERT INTO episodes(id, show_id, episode_number, first_video_id,
                                     first_account_id, published_at, created_at)
                SELECT id, show_id, episode_number, first_video_id,
                       first_account_id, published_at, created_at
                FROM episodes_v9_source;
                CREATE TABLE episode_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                    video_id INTEGER NOT NULL UNIQUE REFERENCES videos(id) ON DELETE CASCADE,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    published_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(episode_id, video_id)
                );
                INSERT INTO episode_sources
                SELECT * FROM episode_sources_v9_source;
                DROP TABLE episode_sources_v9_source;
                DROP TABLE episodes_v9_source;
                UPDATE app_meta SET value = '8' WHERE key = 'schema_version';
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = ShortDramaRepository(database_path)
        episode = migrated.get_show_episodes(show["id"])[0]
        self.assertEqual(episode["season_number"], 1)
        self.assertEqual(episode["id"], write.episode["id"])
        self.assertEqual(
            migrated.get_episode_sources(episode["id"])[0]["video_id"], video["id"]
        )

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

    def test_later_recorded_earlier_source_repairs_episode_first_source(self):
        account = self.create_account()
        show = self.repository.create_show(title="契鬼人", normalized_title="契鬼人")
        later_video = self.create_video_at(account["id"], "first-late", "2026/08/04")
        first = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=1,
            video_id=later_video["id"],
            account_id=account["id"],
            published_at="2026/08/04",
        )
        earlier_video = self.create_video_at(account["id"], "first-early", "2026/07/07")
        second = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=1,
            video_id=earlier_video["id"],
            account_id=account["id"],
            published_at="2026/07/07",
        )

        self.assertTrue(first.is_new_episode)
        self.assertFalse(second.is_new_episode)
        episode = self.repository.get_show_episodes(show["id"])[0]
        self.assertEqual(episode["published_at"], "2026/07/07")
        self.assertEqual(episode["first_video_id"], earlier_video["id"])
        self.assertEqual(episode["first_account_id"], account["id"])
        self.assertEqual(self.repository.get_show(show["id"])["latest_update_at"], "2026/07/07")

    def test_merge_show_moves_non_overlapping_episodes_and_keeps_source_title_as_alias(self):
        account = self.create_account()
        target = self.repository.create_show(title="契鬼人", normalized_title="契鬼人")
        source = self.repository.create_show(title="契鬼人 I", normalized_title="契鬼人i")
        for number in range(7, 12):
            video = self.create_video_at(
                account["id"], f"target-{number}", f"2026-08-{number:02d}T00:00:00+00:00"
            )
            self.repository.record_episode_source(
                show_id=target["id"],
                episode_number=number,
                video_id=video["id"],
                account_id=account["id"],
                published_at=video["publish_time"],
            )
        source_videos = []
        for number in range(1, 7):
            video = self.create_video_at(
                account["id"], f"source-{number}", f"2026-07-{number:02d}T00:00:00+00:00"
            )
            source_videos.append(video)
            self.repository.record_episode_source(
                show_id=source["id"],
                episode_number=number,
                video_id=video["id"],
                account_id=account["id"],
                published_at=video["publish_time"],
            )
        self.repository.update_video_processing(
            source_videos[0]["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=0.9,
            parsed_show_title=source["title"],
            parsed_episode_number=1,
            classification_status="matched",
            content_type="episode",
        )

        merged = self.repository.merge_show(source["id"], target["id"])

        self.assertEqual(merged["id"], target["id"])
        self.assertIsNone(self.repository.get_show(source["id"]))
        self.assertEqual(
            sorted(episode["episode_number"] for episode in self.repository.get_show_episodes(target["id"])),
            list(range(1, 12)),
        )
        self.assertEqual(merged["latest_episode"], 11)
        self.assertIn(source["title"], merged["aliases"])
        self.assertEqual(self.repository.get_video(source_videos[0]["id"])["parsed_show_title"], target["title"])

    def test_merge_show_coalesces_same_episode_and_preserves_sources_and_notifications(self):
        first_account = self.create_account("merge-sec-1")
        second_account = self.create_account("merge-sec-2")
        target = self.repository.create_show(title="契鬼人", normalized_title="契鬼人")
        source = self.repository.create_show(title="契鬼人 I", normalized_title="契鬼人i")
        target_video = self.create_video_at(
            first_account["id"], "merge-target-6", "2026-08-04T00:00:00+00:00"
        )
        target_write = self.repository.record_episode_source(
            show_id=target["id"],
            episode_number=6,
            video_id=target_video["id"],
            account_id=first_account["id"],
            published_at=target_video["publish_time"],
        )
        source_video = self.create_video_at(
            second_account["id"], "merge-source-6", "2026-07-07T00:00:00+00:00"
        )
        source_write = self.repository.record_episode_source(
            show_id=source["id"],
            episode_number=6,
            video_id=source_video["id"],
            account_id=second_account["id"],
            published_at=source_video["publish_time"],
        )
        notification = self.repository.record_notification(
            show_id=source["id"],
            episode_id=source_write.episode["id"],
            channel="test",
            success=True,
        )

        self.repository.merge_show(source["id"], target["id"])

        episodes = self.repository.get_show_episodes(target["id"])
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["id"], target_write.episode["id"])
        self.assertEqual(episodes[0]["published_at"], "2026-07-07T00:00:00+00:00")
        self.assertEqual(
            {item["video_id"] for item in self.repository.get_episode_sources(episodes[0]["id"])},
            {target_video["id"], source_video["id"]},
        )
        notifications = self.repository.list_notifications(episode_id=episodes[0]["id"])
        self.assertEqual(notifications[0]["id"], notification["id"])
        self.assertEqual(notifications[0]["show_id"], target["id"])

    def test_merge_show_rolls_back_every_change_when_source_move_fails(self):
        account = self.create_account()
        target = self.repository.create_show(title="保留剧", normalized_title="保留剧")
        source = self.repository.create_show(title="待合并剧", normalized_title="待合并剧")
        target_video = self.create_video_at(
            account["id"], "rollback-target", "2026-08-04T00:00:00+00:00"
        )
        self.repository.record_episode_source(
            show_id=target["id"],
            episode_number=1,
            video_id=target_video["id"],
            account_id=account["id"],
            published_at=target_video["publish_time"],
        )
        source_video = self.create_video_at(
            account["id"], "rollback-source", "2026-08-03T00:00:00+00:00"
        )
        source_write = self.repository.record_episode_source(
            show_id=source["id"],
            episode_number=1,
            video_id=source_video["id"],
            account_id=account["id"],
            published_at=source_video["publish_time"],
        )
        with self.repository._transaction() as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_episode_source_move
                BEFORE UPDATE OF episode_id ON episode_sources
                BEGIN
                    SELECT RAISE(ABORT, 'forced merge failure');
                END
                """
            )

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.merge_show(source["id"], target["id"])

        self.assertEqual(self.repository.get_show(target["id"])["aliases"], [])
        self.assertIsNotNone(self.repository.get_show(source["id"]))
        self.assertEqual(
            self.repository.get_episode_sources(source_write.episode["id"])[0]["video_id"],
            source_video["id"],
        )

    def test_renaming_show_updates_normalized_title(self):
        show = self.repository.create_show(title="旧剧名", normalized_title="旧剧名")

        updated = self.repository.update_show(show["id"], title="新 剧 名")

        self.assertEqual(updated["title"], "新 剧 名")
        self.assertEqual(updated["normalized_title"], "新剧名")

    def test_renaming_show_to_conflicting_normalized_title_requires_merge(self):
        canonical = self.repository.create_show(title="契鬼人", normalized_title="契鬼人")
        duplicate = self.repository.create_show(title="其它短剧", normalized_title="其它短剧")

        with self.assertRaisesRegex(ValueError, "合并"):
            self.repository.update_show(duplicate["id"], title="契 鬼 人！")

        still_duplicate = self.repository.get_show(duplicate["id"])
        self.assertEqual(still_duplicate["title"], "其它短剧")
        self.assertEqual(still_duplicate["normalized_title"], "其它短剧")
        self.assertIsNotNone(self.repository.get_show(canonical["id"]))

    def test_repair_episode_and_show_consistency_is_idempotent_for_legacy_data(self):
        account = self.create_account()
        show = self.repository.create_show(title="历史修复", normalized_title="历史修复")
        late_video = self.create_video_at(account["id"], "repair-late", "2026/08/04")
        episode_one = self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=1,
            video_id=late_video["id"],
            account_id=account["id"],
            published_at="2026/08/04",
        )
        early_video = self.create_video_at(account["id"], "repair-early", "2026/07/07")
        self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=1,
            video_id=early_video["id"],
            account_id=account["id"],
            published_at="2026/07/07",
        )
        latest_video = self.create_video_at(account["id"], "repair-latest", "2026/07/08")
        self.repository.record_episode_source(
            show_id=show["id"],
            episode_number=2,
            video_id=latest_video["id"],
            account_id=account["id"],
            published_at="2026/07/08",
        )
        with self.repository._transaction() as connection:
            connection.execute(
                """
                UPDATE episodes
                SET first_video_id = ?, first_account_id = ?, published_at = ?
                WHERE id = ?
                """,
                (late_video["id"], account["id"], "2026/08/04", episode_one.episode["id"]),
            )
            connection.execute(
                """
                UPDATE shows
                SET latest_episode = ?, latest_update_at = ?
                WHERE id = ?
                """,
                (1, "2026/08/04", show["id"]),
            )

        repaired = self.repository.repair_episode_and_show_consistency()
        repaired_episode = self.repository.get_show_episodes(show["id"])[1]
        repaired_show = self.repository.get_show(show["id"])
        second_run = self.repository.repair_episode_and_show_consistency()

        self.assertGreaterEqual(repaired["episodes_repaired"], 1)
        self.assertGreaterEqual(repaired["shows_repaired"], 1)
        self.assertEqual(repaired_episode["first_video_id"], early_video["id"])
        self.assertEqual(repaired_episode["published_at"], "2026/07/07")
        self.assertEqual(repaired_show["latest_episode"], 2)
        self.assertEqual(repaired_show["latest_update_at"], "2026/07/08")
        self.assertEqual(second_run["episodes_repaired"], 0)
        self.assertEqual(second_run["shows_repaired"], 0)

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

    def test_history_sync_state_is_additive_and_can_pause_resume(self):
        account = self.create_account("history-sec")
        self.assertEqual(account["history_sync_status"], "idle")
        self.assertTrue(account["history_has_more"])

        started = self.repository.start_history_backfill(account["id"])
        self.assertEqual(started["history_sync_status"], "pending")
        self.assertEqual(started["history_sync"]["next_cursor"], 0)

        progressed = self.repository.update_history_sync_state(
            account["id"],
            status="running",
            next_cursor=50,
            has_more=True,
            processed_pages=1,
            scanned_items=50,
            new_videos=30,
            started_at=started["history_started_at"],
        )
        paused = self.repository.pause_history_backfill(account["id"])
        resumed = self.repository.resume_history_backfill(account["id"])

        self.assertEqual(progressed["history_sync"]["next_cursor"], 50)
        self.assertEqual(paused["history_sync_status"], "paused")
        self.assertEqual(resumed["history_sync_status"], "pending")
        self.assertEqual(resumed["history_sync"]["scanned_items"], 50)

    def test_scan_runs_are_listed_summarized_and_pruned(self):
        account = self.create_account("scan-run")
        self.repository.record_scan_run(account["id"], success=1, trigger_type="manual", new_videos=2, new_episodes=1)
        with self.repository._transaction() as connection:
            connection.execute("UPDATE scan_runs SET created_at='2020-01-01', started_at='2020-01-01'")
        self.assertEqual(len(self.repository.list_scan_runs(account["id"])), 1)
        self.assertEqual(self.repository.prune_scan_runs(retention_days=30), 1)
        self.assertEqual(self.repository.system_status()["scan_runs_24h"]["runs"], 0)

    def test_show_detail_reports_database_episode_gaps(self):
        account = self.create_account("gap-sec")
        show = self.repository.create_show(title="缺集短剧", normalized_title="缺集短剧")
        for number in (1, 2, 3, 5, 6, 8):
            video = self.create_video(account["id"], f"gap-{number}")
            self.repository.record_episode_source(
                show_id=show["id"],
                episode_number=number,
                video_id=video["id"],
                account_id=account["id"],
                published_at=video["publish_time"],
            )

        detail = self.repository.get_show_detail(show["id"])

        self.assertEqual(detail["missing_episode_numbers"], [4, 7])

    def test_show_library_summaries_report_real_progress_and_filter_by_any_source(self):
        first = self.create_account("library-a")
        second = self.create_account("library-b")
        show = self.repository.create_show(
            title="归墟", normalized_title="归墟", aliases=["归墟系列"]
        )
        for number, account in ((0, first), (1, first), (3, second)):
            video = self.create_video(account["id"], f"library-{number}")
            self.repository.record_episode_source(
                show_id=show["id"],
                episode_number=number,
                video_id=video["id"],
                account_id=account["id"],
                published_at=video["publish_time"],
            )

        summary = self.repository.list_show_summaries()[0]
        self.assertEqual(summary["episode_count"], 3)
        self.assertEqual(summary["regular_episode_count"], 2)
        self.assertEqual(summary["special_episode_count"], 1)
        self.assertEqual(summary["min_episode"], 0)
        self.assertEqual(summary["max_episode"], 3)
        self.assertEqual(summary["missing_episode_count"], 1)
        self.assertEqual(summary["source_account_count"], 2)
        self.assertEqual({item["id"] for item in summary["source_accounts"]}, {first["id"], second["id"]})
        self.assertEqual(
            [item["id"] for item in self.repository.list_show_summaries(account_id=first["id"])],
            [show["id"]],
        )
        self.assertEqual(
            [item["id"] for item in self.repository.list_show_summaries(account_id=second["id"])],
            [show["id"]],
        )
        self.assertEqual(
            [item["id"] for item in self.repository.list_show_summaries(q="归墟系列")],
            [show["id"]],
        )

    def test_expected_episode_count_can_be_set_and_cleared(self):
        show = self.repository.create_show(title="总集数", normalized_title="总集数")
        updated = self.repository.update_show(show["id"], expected_episode_count=30)
        cleared = self.repository.update_show(show["id"], expected_episode_count=None)

        self.assertEqual(updated["expected_episode_count"], 30)
        self.assertIsNone(cleared["expected_episode_count"])
        with self.assertRaisesRegex(ValueError, "正整数"):
            self.repository.update_show(show["id"], expected_episode_count=0)

    def test_ignored_shows_are_hidden_from_library_and_parser_candidates_until_restored(self):
        account = self.create_account("ignored-library")
        show = self.repository.create_show(title="活动名称", normalized_title="活动名称")
        video = self.create_video(account["id"], "ignored-library-video")
        self.repository.record_episode_source(
            show_id=show["id"], episode_number=1, video_id=video["id"],
            account_id=account["id"], published_at=video["publish_time"],
        )

        ignored = self.repository.ignore_show(show["id"], reason="不是短剧")
        self.assertTrue(ignored["is_ignored"])
        self.assertEqual(self.repository.list_show_summaries(), [])
        self.assertEqual(self.repository.list_show_candidates(), [])
        self.assertEqual(
            self.repository.list_show_summaries(ignored="ignored")[0]["id"], show["id"]
        )
        with self.assertRaisesRegex(ValueError, "永久忽略"):
            another = self.create_video(account["id"], "ignored-library-video-2")
            self.repository.record_episode_source(
                show_id=show["id"], episode_number=2, video_id=another["id"],
                account_id=account["id"], published_at=another["publish_time"],
            )

        restored = self.repository.restore_show(show["id"])
        self.assertFalse(restored["is_ignored"])
        self.assertEqual(self.repository.list_show_candidates()[0]["id"], show["id"])

    def test_remove_episode_and_source_preserve_videos_and_refresh_show(self):
        first = self.create_account("remove-a")
        second = self.create_account("remove-b")
        show = self.repository.create_show(title="归墟", normalized_title="归墟")
        first_video = self.create_video(first["id"], "remove-source-a")
        second_video = self.create_video(second["id"], "remove-source-b")
        final_video = self.create_video(first["id"], "remove-episode")
        first_write = self.repository.record_episode_source(
            show_id=show["id"], episode_number=1, video_id=first_video["id"],
            account_id=first["id"], published_at=first_video["publish_time"],
        )
        self.repository.record_episode_source(
            show_id=show["id"], episode_number=1, video_id=second_video["id"],
            account_id=second["id"], published_at=second_video["publish_time"],
        )
        final_write = self.repository.record_episode_source(
            show_id=show["id"], episode_number=9, video_id=final_video["id"],
            account_id=first["id"], published_at=final_video["publish_time"],
        )

        sources = self.repository.get_episode_sources(first_write.episode["id"])
        removed_source = self.repository.remove_episode_source(
            show["id"], first_write.episode["id"], sources[1]["id"]
        )
        self.assertFalse(removed_source["episode_removed"])
        self.assertEqual(
            self.repository.get_video(second_video["id"])["parser_reason"],
            "manual_remove_source",
        )
        removed_episode = self.repository.remove_episode(show["id"], final_write.episode["id"])
        self.assertEqual(removed_episode["show"]["latest_episode"], 1)
        self.assertEqual(
            self.repository.get_video(final_video["id"])["parser_reason"],
            "manual_remove_episode",
        )
        self.assertIsNotNone(self.repository.get_video(final_video["id"]))

    def test_v7_database_migrates_show_library_columns(self):
        database_path = self.root / "v7.db"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO app_meta(key, value) VALUES ('schema_version', '7');
                CREATE TABLE shows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL UNIQUE,
                    aliases TEXT NOT NULL DEFAULT '[]',
                    latest_episode INTEGER,
                    latest_update_at TEXT,
                    status TEXT NOT NULL DEFAULT 'updating',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO shows(title, normalized_title, created_at, updated_at)
                VALUES ('旧短剧', '旧短剧', '2026-01-01', '2026-01-01');
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = ShortDramaRepository(database_path)
        show = migrated.get_show(1)
        self.assertIsNone(show["expected_episode_count"])
        self.assertFalse(show["is_ignored"])

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

    def test_video_search_paginates_and_combines_filters(self):
        account = self.create_account("video-search")
        for number in range(3):
            video = self.create_video(account["id"], f"search-{number}")
            self.repository.update_video_processing(video["id"], is_processed=True, needs_review=False, classification_status="ignored", parser_confidence=0, parser_method="regex:test", content_type="non_drama")
        first = self.repository.search_videos(account_id=account["id"], parser_method="regex:test", content_type="non_drama", q="search", page=1, page_size=2)
        empty = self.repository.search_videos(account_id=account["id"], page=3, page_size=2)
        self.assertEqual((first["total"], first["total_pages"], len(first["videos"])), (3, 2, 2))
        self.assertEqual(empty["videos"], [])

    def test_reparse_scopes_select_legacy_ignored_and_all_candidates(self):
        account = self.create_account("reparse-sec")
        legacy = self.create_video(account["id"], "reparse-legacy")
        current_ignored = self.create_video(account["id"], "reparse-ignored")
        review = self.create_video(account["id"], "reparse-review")
        self.repository.update_video_processing(
            legacy["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=None,
            classification_status="ignored",
            parser_reason="legacy_ignored",
            show_title_candidate="候选剧",
            episode_candidate=39,
            content_type="unknown",
        )
        self.repository.update_video_processing(
            current_ignored["id"],
            is_processed=True,
            needs_review=False,
            parser_confidence=0.0,
            classification_status="ignored",
            parser_reason="no_short_drama_or_episode_signal",
            content_type="non_drama",
        )
        self.repository.update_video_processing(
            review["id"],
            is_processed=False,
            needs_review=True,
            parser_confidence=0.4,
            parsed_episode_number=39,
            parser_method="regex:bare_episode_signal",
            classification_status="review",
            parser_reason="bare_episode_signal_without_show_context",
            episode_candidate=39,
            content_type="unknown",
        )

        self.assertEqual(
            [video["id"] for video in self.repository.list_reparse_videos(account["id"], scope="legacy_ignored")],
            [legacy["id"]],
        )
        self.assertEqual(
            {
                video["id"]
                for video in self.repository.list_reparse_videos(account["id"], scope="ignored")
            },
            {legacy["id"], current_ignored["id"]},
        )
        self.assertEqual(
            {
                video["id"]
                for video in self.repository.list_reparse_videos(account["id"], scope="ignored_review")
            },
            {legacy["id"], current_ignored["id"], review["id"]},
        )
        refreshed = self.repository.get_video(legacy["id"])
        self.assertEqual(refreshed["episode_candidate"], 39)
        self.assertEqual(refreshed["show_title_candidate"], "候选剧")


if __name__ == "__main__":
    unittest.main()
