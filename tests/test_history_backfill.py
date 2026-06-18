import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from douyin_user_monitor.api import monitor as monitor_api
from douyin_user_monitor.main import app
from douyin_user_monitor.monitor.history_sync import (
    HISTORY_SYNC_STATUS_PENDING,
    HISTORY_SYNC_STATUS_RUNNING,
    build_history_sync_state,
)
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.service import MonitorService
from douyin_user_monitor.monitor.storage import MonitorStorage
from douyin_user_monitor.monitor.user_sync import UserSyncService


class FakeBackfillCrawler:
    def __init__(self):
        self.posts = [
            {"aweme_id": f"aweme-{index:03d}", "create_time": 10_000 - index}
            for index in range(1, 121)
        ]
        self.fetch_calls = []

    async def handler_user_profile(self, sec_user_id: str):
        return {"user": {"nickname": f"昵称-{sec_user_id}"}}

    async def get_sec_user_id(self, url: str) -> str:
        return url

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id
        self.fetch_calls.append((max_cursor, count))
        if count == 20 and max_cursor == 0:
            return {"aweme_list": self.posts[:20]}
        if count == 50 and max_cursor == 0:
            return {"aweme_list": self.posts[:50], "has_more": 1, "max_cursor": 50}
        if count == 50 and max_cursor == 50:
            return {"aweme_list": self.posts[50:100], "has_more": 1, "max_cursor": 100}
        if count == 50 and max_cursor == 100:
            return {"aweme_list": self.posts[100:], "has_more": 0, "max_cursor": 120}
        return {"aweme_list": [], "has_more": 0, "max_cursor": max_cursor}

    async def fetch_one_video(self, aweme_id: str):
        return {
            "aweme_detail": {
                "aweme_id": aweme_id,
                "desc": aweme_id,
                "create_time": 1_710_000_000,
                "video": {"duration": 1000},
            }
        }

    async def get_douyin_headers(self):
        return {"headers": {"User-Agent": "test"}}


class FakeDownloader:
    async def download_aweme_assets(self, **kwargs):
        aweme_id = kwargs["aweme_id"]
        return {
            "media_type": "video",
            "files": [f"{aweme_id}.mp4"],
            "downloaded_file_count": 1,
            "existing_file_count": 0,
            "image_count": 0,
            "total_size_bytes": 128,
        }


class CountingNotifier(NoopMonitorNotifier):
    def __init__(self):
        self.detected = []
        self.completed = []

    async def notify_new_aweme_detected(self, *, user_nickname: str, aweme_detail):
        self.detected.append((user_nickname, aweme_detail["aweme_id"]))

    async def notify_download_completed(self, *, user_nickname: str, record):
        self.completed.append((user_nickname, record["aweme_id"]))


class UserSyncHistoryBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_one_user_backfills_first_page_without_duplicate_notifications(self):
        crawler = FakeBackfillCrawler()
        notifier = CountingNotifier()
        service = UserSyncService(
            crawler=crawler,
            downloader=FakeDownloader(),
            notifier=notifier,
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "测试用户",
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": build_history_sync_state(status=HISTORY_SYNC_STATUS_PENDING, page_size=50),
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(summary["downloaded_items"], 50)
        self.assertEqual(len(user["downloaded_aweme_ids"]), 50)
        self.assertEqual(len(user["download_records"]), 50)
        self.assertEqual(user["history_sync"]["status"], HISTORY_SYNC_STATUS_RUNNING)
        self.assertEqual(user["history_sync"]["next_cursor"], 50)
        self.assertEqual(user["history_sync"]["processed_pages"], 1)
        self.assertEqual(user["history_sync"]["scanned_items"], 50)
        self.assertEqual(user["history_sync"]["downloaded_items"], 30)
        self.assertEqual(len(notifier.detected), 20)
        self.assertEqual(len(notifier.completed), 20)
        self.assertEqual(crawler.fetch_calls, [(0, 20), (0, 50)])


class MonitorServiceHistoryBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_user_initializes_pending_history_sync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            service = MonitorService(
                crawler=FakeBackfillCrawler(),
                storage=MonitorStorage(temp_path / "monitor_users.json"),
                download_root=temp_path / "download",
                notifier=NoopMonitorNotifier(),
            )

            result = await service.add_user_with_status("sec-user-1")

            self.assertEqual(result["status"], "created")
            history_sync = result["user"]["history_sync"]
            self.assertEqual(history_sync["status"], HISTORY_SYNC_STATUS_PENDING)
            self.assertEqual(history_sync["page_size"], 50)
            self.assertTrue(history_sync["has_more"])


class HistoryBackfillRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_start_history_backfill_route_returns_success(self):
        user = {"id": "user-1", "history_sync": {"status": "pending"}}
        with patch.object(monitor_api.monitor_service, "start_user_history_backfill", return_value=user):
            response = self.client.post("/api/monitor/users/user-1/backfill/start")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["id"], "user-1")


if __name__ == "__main__":
    unittest.main()
