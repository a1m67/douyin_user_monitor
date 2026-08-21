import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository


class SQLitePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.database = Path(cls.temp.name) / "synthetic.db"
        cls.repository = ShortDramaRepository(cls.database)
        cls._populate_synthetic_database()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def _populate_synthetic_database(cls):
        timestamp = "2026-08-01T00:00:00+00:00"
        accounts = [
            (
                f"account-{number}",
                f"Account {number}",
                f"sec-{number}",
                f"https://www.douyin.com/user/sec-{number}",
                timestamp,
                timestamp,
            )
            for number in range(100)
        ]
        videos = [
            (
                f"aweme-{number}",
                f"account-{number % 100}",
                f"2026-08-{number % 28 + 1:02d}T00:00:00+00:00",
                timestamp,
            )
            for number in range(50_000)
        ]
        shows = [
            (
                f"Show {number}",
                f"show-{number}",
                number % 2,
                f"2026-08-{number % 28 + 1:02d}T00:00:00+00:00",
                timestamp,
                timestamp,
            )
            for number in range(1_000)
        ]
        episodes = []
        sources = []
        events = []
        for number in range(10_000):
            identifier = number + 1
            show_id = number // 10 + 1
            episode_number = number % 10 + 1
            video_id = identifier
            account_id = f"account-{number % 100}"
            occurred_at = f"2026-08-{number % 28 + 1:02d}T00:00:00+00:00"
            episodes.append(
                (
                    show_id,
                    1,
                    episode_number,
                    video_id,
                    account_id,
                    occurred_at,
                    timestamp,
                )
            )
            sources.append(
                (identifier, video_id, account_id, occurred_at, timestamp)
            )
            events.append(
                (
                    show_id,
                    identifier,
                    1,
                    episode_number,
                    account_id,
                    video_id,
                    occurred_at,
                    timestamp if number % 2 else None,
                    timestamp,
                )
            )
        with cls.repository._transaction() as connection:
            connection.executemany(
                """
                INSERT INTO accounts(id,nickname,sec_uid,homepage_url,created_at,updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                accounts,
            )
            connection.executemany(
                """
                INSERT INTO videos(aweme_id,account_id,publish_time,created_at)
                VALUES (?,?,?,?)
                """,
                videos,
            )
            connection.executemany(
                """
                INSERT INTO shows(
                    title,normalized_title,is_following,latest_update_at,created_at,updated_at
                ) VALUES (?,?,?,?,?,?)
                """,
                shows,
            )
            connection.executemany(
                """
                INSERT INTO episodes(
                    show_id,season_number,episode_number,first_video_id,
                    first_account_id,published_at,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                episodes,
            )
            connection.executemany(
                """
                INSERT INTO episode_sources(
                    episode_id,video_id,account_id,published_at,created_at
                ) VALUES (?,?,?,?,?)
                """,
                sources,
            )
            connection.executemany(
                """
                INSERT INTO update_events(
                    show_id,episode_id,season_number,episode_number,account_id,
                    video_id,occurred_at,read_at,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                events,
            )

    def test_connections_enable_wal_busy_timeout_and_foreign_keys(self):
        with self.repository._transaction() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 30_000)
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_read_transaction_does_not_hold_process_write_lock(self):
        with self.repository._transaction() as connection:
            connection.execute("SELECT 1").fetchone()
            self.assertFalse(self.repository._lock._is_owned())
        with self.repository._transaction() as connection:
            connection.execute("INSERT INTO app_meta(key, value) VALUES ('phase6_lock_test', '1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            self.assertTrue(self.repository._lock._is_owned())

    def test_video_and_update_pagination_remain_bounded(self):
        videos = self.repository.search_videos(page=2, page_size=25)
        updates = self.repository.list_update_events(
            following_only=False, unread_only=True, page=2, page_size=40
        )
        self.assertEqual(videos["total"], 50_000)
        self.assertEqual(len(videos["videos"]), 25)
        self.assertEqual(videos["page"], 2)
        self.assertEqual(updates["total"], 5_000)
        self.assertEqual(len(updates["events"]), 40)
        self.assertTrue(updates["has_more"])

    def test_show_and_global_search_results_remain_bounded(self):
        shows = self.repository.paginate_show_summaries(
            include_empty=True,
            page=2,
            page_size=25,
        )
        show_search = self.repository.search_global("Show 999", limit=5)
        account_search = self.repository.search_global("Account 99", limit=5)
        video_search = self.repository.search_global("aweme-49999", limit=5)

        self.assertEqual(shows["total"], 1_000)
        self.assertEqual(len(shows["shows"]), 25)
        self.assertEqual(shows["page"], 2)
        self.assertEqual(show_search["results"]["shows"][0]["title"], "Show 999")
        self.assertEqual(account_search["results"]["accounts"][0]["nickname"], "Account 99")
        self.assertEqual(video_search["results"]["videos"][0]["aweme_id"], "aweme-49999")
        self.assertLessEqual(len(video_search["results"]["videos"]), 5)

    def test_key_query_plans_use_ordered_indexes(self):
        queries = {
            "videos": """
                SELECT videos.id FROM videos
                JOIN accounts ON accounts.id=videos.account_id
                LEFT JOIN episode_sources es ON es.video_id=videos.id
                LEFT JOIN episodes e ON e.id=es.episode_id
                LEFT JOIN shows ON shows.id=e.show_id
                ORDER BY COALESCE(videos.publish_time,videos.created_at) DESC,videos.id DESC
                LIMIT 50 OFFSET 0
            """,
            "updates": """
                SELECT update_events.id FROM update_events
                JOIN shows ON shows.id=update_events.show_id
                JOIN accounts ON accounts.id=update_events.account_id
                JOIN videos ON videos.id=update_events.video_id
                WHERE update_events.read_at IS NULL
                ORDER BY update_events.occurred_at DESC,update_events.id DESC
                LIMIT 50 OFFSET 0
            """,
        }
        with self.repository._transaction() as connection:
            plans = {
                name: "\n".join(
                    str(row["detail"])
                    for row in connection.execute(f"EXPLAIN QUERY PLAN {query}")
                )
                for name, query in queries.items()
            }
        self.assertIn("idx_videos_published", plans["videos"])
        self.assertIn("idx_update_events_unread_order", plans["updates"])
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plans["videos"])
        self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", plans["updates"])

    def test_show_detail_batches_episode_sources_in_one_connection(self):
        original_connect = self.repository._connect
        connection_count = 0

        def counting_connect():
            nonlocal connection_count
            connection_count += 1
            return original_connect()

        self.repository._connect = counting_connect
        try:
            detail = self.repository.get_show_detail(1)
        finally:
            self.repository._connect = original_connect
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["episodes"]), 10)
        self.assertTrue(all(len(episode["sources"]) == 1 for episode in detail["episodes"]))
        self.assertEqual(connection_count, 1)

    def test_quality_items_are_bounded_while_counts_remain_complete(self):
        with self.repository._transaction() as connection:
            connection.execute(
                """
                UPDATE videos SET classification_status='review', needs_review=1
                WHERE id <= 100
                """
            )
        report = self.repository.data_quality_report(limit=3)
        self.assertEqual(report["categories"]["review"]["count"], 100)
        self.assertEqual(len(report["categories"]["review"]["items"]), 3)
        self.assertTrue(
            all(len(category["items"]) <= 3 for category in report["categories"].values())
        )


if __name__ == "__main__":
    unittest.main()
