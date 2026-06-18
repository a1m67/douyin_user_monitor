import unittest

from douyin_user_monitor.monitor.history_sync import (
    HISTORY_SYNC_STATUS_COMPLETED,
    HISTORY_SYNC_STATUS_PENDING,
    build_history_sync_state,
)
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.user_sync import UserSyncService


class NullAwemeListCrawler:
    async def handler_user_profile(self, sec_user_id: str):
        return {"user": {"nickname": f"昵称-{sec_user_id}"}}

    async def get_sec_user_id(self, url: str) -> str:
        return url

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id, max_cursor, count
        return {"aweme_list": None, "has_more": 0, "max_cursor": max_cursor}

    async def fetch_one_video(self, aweme_id: str):
        _ = aweme_id
        return {}

    async def get_douyin_headers(self):
        return {"headers": {}}


class InvalidAwemeListCrawler(NullAwemeListCrawler):
    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id, max_cursor, count
        return {"aweme_list": {"unexpected": True}}


class UserSyncAwemeListTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_one_user_treats_null_aweme_list_as_empty(self):
        service = UserSyncService(
            crawler=NullAwemeListCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "测试用户",
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": build_history_sync_state(
                status=HISTORY_SYNC_STATUS_PENDING,
                page_size=50,
            ),
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(summary["errors"], [])
        self.assertIsNone(user["last_error"])
        self.assertEqual(user["downloaded_count"], 0)
        self.assertEqual(user["history_sync"]["status"], HISTORY_SYNC_STATUS_COMPLETED)

    async def test_sync_one_user_keeps_rejecting_non_list_aweme_list(self):
        service = UserSyncService(
            crawler=InvalidAwemeListCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "测试用户",
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": build_history_sync_state(
                status=HISTORY_SYNC_STATUS_PENDING,
                page_size=50,
            ),
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("接口返回的 aweme_list 不是列表", user["last_error"])


if __name__ == "__main__":
    unittest.main()
