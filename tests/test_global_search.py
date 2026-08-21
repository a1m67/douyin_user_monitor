from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douyin_user_monitor.repositories.sqlite import SCHEMA_VERSION, ShortDramaRepository


class GlobalSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repository = ShortDramaRepository(self.root / "app.db")
        self.account = self.repository.create_account(
            sec_uid="search-sec",
            nickname="星河剧场",
            homepage_url="https://www.douyin.com/user/search-sec",
            avatar_url="https://img.example/avatar.jpg",
        )
        self.show = self.repository.create_show(
            title="归墟",
            normalized_title="归墟",
            aliases=["归墟系列"],
        )
        self.video, _ = self.repository.create_video(
            aweme_id="search-aweme-1001",
            account_id=self.account["id"],
            description="归墟终章已经更新",
            display_title="归墟大结局",
            hashtags=["归墟"],
            publish_time="2026-08-22T00:00:00+00:00",
            video_url="https://www.douyin.com/video/search-aweme-1001",
            cover_url="https://img.example/cover.jpg",
            raw={"private_payload": "must-not-leak"},
        )
        with self.repository._transaction() as connection:
            connection.execute(
                "UPDATE videos SET parsed_show_title='归墟正传' WHERE id=?",
                (self.video["id"],),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_show_pagination_returns_bounded_metadata(self):
        for number in range(4):
            self.repository.create_show(
                title=f"分页短剧 {number}",
                normalized_title=f"分页短剧-{number}",
            )

        first = self.repository.paginate_show_summaries(
            include_empty=True,
            sort="title",
            page=1,
            page_size=2,
        )
        third = self.repository.paginate_show_summaries(
            include_empty=True,
            sort="title",
            page=3,
            page_size=2,
        )

        self.assertEqual(first["total"], 5)
        self.assertEqual(first["total_pages"], 3)
        self.assertEqual(len(first["shows"]), 2)
        self.assertEqual(third["page"], 3)
        self.assertEqual(len(third["shows"]), 1)
        self.assertEqual(len(self.repository.list_show_summaries(include_empty=True, limit=3)), 3)

    def test_fts_search_covers_all_entities_and_returns_safe_fields(self):
        if not self.repository.search_index_status()["fts5_available"]:
            self.skipTest("SQLite runtime does not provide FTS5")

        show = self.repository.search_global("归墟系列")
        account = self.repository.search_global("星河剧场")
        by_title = self.repository.search_global("归墟大结局")
        by_description = self.repository.search_global("归墟终章")
        by_parsed_title = self.repository.search_global("归墟正传")
        by_aweme = self.repository.search_global("search-aweme-1001")

        self.assertEqual(show["mode"], "fts5")
        self.assertEqual(show["results"]["shows"][0]["id"], self.show["id"])
        self.assertEqual(account["results"]["accounts"][0]["id"], self.account["id"])
        for result in (by_title, by_description, by_parsed_title, by_aweme):
            self.assertEqual(result["results"]["videos"][0]["id"], self.video["id"])
            self.assertNotIn("raw_json", result["results"]["videos"][0])
            self.assertNotIn("llm_raw_result", result["results"]["videos"][0])

        only_accounts = self.repository.search_global(
            "星河",
            types=["accounts"],
            limit=1,
        )
        self.assertEqual(set(only_accounts["results"]), {"accounts"})
        with self.assertRaisesRegex(ValueError, "类型"):
            self.repository.search_global("归墟", types=["secrets"])

    def test_like_fallback_works_without_fts5(self):
        fallback_path = self.root / "fallback.db"
        with patch.object(ShortDramaRepository, "_supports_fts5", return_value=False):
            repository = ShortDramaRepository(fallback_path)
        account = repository.create_account(
            sec_uid="fallback-sec",
            nickname="无索引作者",
            homepage_url="https://www.douyin.com/user/fallback-sec",
        )
        show = repository.create_show(
            title="回声",
            normalized_title="回声",
            aliases=["回声计划"],
        )
        video, _ = repository.create_video(
            aweme_id="fallback-aweme",
            account_id=account["id"],
            description="回声计划第1集",
            hashtags=[],
            publish_time=None,
            video_url="",
            cover_url=None,
            raw={},
        )

        result = repository.search_global("回声计划")

        self.assertEqual(repository.schema_version(), SCHEMA_VERSION)
        self.assertEqual(result["mode"], "like")
        self.assertEqual(result["results"]["shows"][0]["id"], show["id"])
        self.assertEqual(result["results"]["videos"][0]["id"], video["id"])

    def test_rebuild_restores_basic_index_consistency(self):
        if not self.repository.search_index_status()["fts5_available"]:
            self.skipTest("SQLite runtime does not provide FTS5")
        with self.repository._transaction() as connection:
            connection.execute(
                "DELETE FROM search_videos WHERE entity_id=?",
                (str(self.video["id"]),),
            )
        self.assertFalse(self.repository.search_index_status()["consistent"])

        rebuilt = self.repository.rebuild_search_index()

        self.assertTrue(rebuilt["rebuilt"])
        self.assertTrue(rebuilt["consistent"])


if __name__ == "__main__":
    unittest.main()
