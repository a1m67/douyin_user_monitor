import unittest
import tempfile
from pathlib import Path

from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.service import MonitorService
from douyin_user_monitor.monitor.storage import MonitorStorage, build_default_state
from douyin_user_monitor.monitor.profile_parser import extract_account_status
from douyin_user_monitor.monitor.user_sync import UserSyncService


DELETED_PROFILE = {
    "status_code": 0,
    "user": {
        "nickname": "",
        "user_deleted": True,
        "special_state_info": {
            "title": "账号已经注销",
            "content": "用户已将自己账号注销，所属内容已无法查看",
        },
    },
}

BANNED_PROFILE = {
    "status_code": 0,
    "status_msg": "该账号因违规已封禁",
    "user": {
        "nickname": "测试账号",
        "special_state_info": {
            "title": "账号已封禁",
            "content": "由于违规行为，该账号已被封禁",
        },
    },
}

NON_BANNED_PROFILE = {
    "status_code": 0,
    "status_msg": "未封禁，可正常使用",
    "user": {
        "nickname": "普通账号",
        "special_state_info": {
            "title": "封禁申诉说明",
            "content": "如果账号被封禁，可查看申诉流程",
        },
    },
}


class DeletedAccountCrawler:
    def __init__(self):
        self.profile_calls = 0

    async def handler_user_profile(self, sec_user_id: str):
        _ = sec_user_id
        self.profile_calls += 1
        return DELETED_PROFILE

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


class ErrorProfileCrawler(DeletedAccountCrawler):
    async def handler_user_profile(self, sec_user_id: str):
        _ = sec_user_id
        self.profile_calls += 1
        raise RuntimeError("上游 400")


class AccountStatusParsingTests(unittest.TestCase):
    def test_extract_account_status_detects_deleted_before_banned(self):
        status = extract_account_status(DELETED_PROFILE)

        self.assertEqual(status["account_status"], "deleted")
        self.assertEqual(status["account_status_label"], "已注销")
        self.assertIn("账号已经注销", str(status["account_status_reason"]))

    def test_extract_account_status_detects_banned_by_explicit_keywords(self):
        status = extract_account_status(BANNED_PROFILE)

        self.assertEqual(status["account_status"], "banned")
        self.assertEqual(status["account_status_label"], "已封禁")
        self.assertIn("封禁", str(status["account_status_reason"]))

    def test_extract_account_status_does_not_guess_banned_from_generic_text(self):
        status = extract_account_status(NON_BANNED_PROFILE)

        self.assertEqual(status["account_status"], "normal")
        self.assertEqual(status["account_status_label"], "正常")
        self.assertIsNone(status["account_status_reason"])


class UserSyncAccountStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_one_user_updates_deleted_account_fields(self):
        service = UserSyncService(
            crawler=DeletedAccountCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "旧昵称",
            "avatar_url": None,
            "account_status": "normal",
            "account_status_label": "正常",
            "account_status_reason": None,
            "account_status_updated_at": None,
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": {"status": "idle", "has_more": False},
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(summary["errors"], [])
        self.assertEqual(user["account_status"], "deleted")
        self.assertEqual(user["account_status_label"], "已注销")
        self.assertIn("账号已经注销", str(user["account_status_reason"]))
        self.assertIsNotNone(user["account_status_updated_at"])


class MonitorServiceAccountHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_users_with_profile_skips_legacy_user_when_name_and_avatar_exist(self):
        crawler = ErrorProfileCrawler()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage = MonitorStorage(temp_path / "monitor_users.json")
            state = build_default_state()
            state["users"].append(
                {
                    "id": "user-legacy",
                    "profile_url": "https://www.douyin.com/user/legacy",
                    "sec_user_id": "sec-legacy",
                    "nickname": "旧用户",
                    "avatar_url": "https://example.com/avatar.jpg",
                    "account_status": "normal",
                    "account_status_label": "正常",
                    "account_status_reason": None,
                    "account_status_updated_at": None,
                    "enabled": True,
                    "created_at": "2026-04-13T12:00:00+08:00",
                    "updated_at": "2026-04-13T12:00:00+08:00",
                    "last_checked_at": None,
                    "last_download_at": None,
                    "last_aweme_id": None,
                    "downloaded_count": 0,
                    "downloaded_aweme_ids": [],
                    "download_records": [],
                    "history_sync": {"status": "idle", "has_more": False},
                    "last_error": None,
                }
            )
            storage.save_state(state)
            service = MonitorService(
                crawler=crawler,
                storage=storage,
                download_root=temp_path / "download",
                notifier=NoopMonitorNotifier(),
            )

            users = await service.list_users_with_profile()

            self.assertEqual(users[0]["nickname"], "旧用户")
            self.assertEqual(crawler.profile_calls, 0)

    async def test_list_users_with_profile_skips_deleted_user_without_avatar_once_status_known(self):
        crawler = DeletedAccountCrawler()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage = MonitorStorage(temp_path / "monitor_users.json")
            state = build_default_state()
            state["users"].append(
                {
                    "id": "user-1",
                    "profile_url": "https://www.douyin.com/user/test",
                    "sec_user_id": "sec-user-1",
                    "nickname": "已注销账号",
                    "avatar_url": None,
                    "account_status": "deleted",
                    "account_status_label": "已注销",
                    "account_status_reason": "账号已经注销",
                    "account_status_updated_at": "2026-04-13T12:00:00+08:00",
                    "enabled": True,
                    "created_at": "2026-04-13T12:00:00+08:00",
                    "updated_at": "2026-04-13T12:00:00+08:00",
                    "last_checked_at": None,
                    "last_download_at": None,
                    "last_aweme_id": None,
                    "downloaded_count": 0,
                    "downloaded_aweme_ids": [],
                    "download_records": [],
                    "history_sync": {"status": "idle", "has_more": False},
                    "last_error": None,
                }
            )
            storage.save_state(state)
            service = MonitorService(
                crawler=crawler,
                storage=storage,
                download_root=temp_path / "download",
                notifier=NoopMonitorNotifier(),
            )

            users = await service.list_users_with_profile()

            self.assertEqual(users[0]["account_status"], "deleted")
            self.assertEqual(crawler.profile_calls, 0)

    async def test_list_users_with_profile_records_error_when_single_user_hydration_fails(self):
        crawler = ErrorProfileCrawler()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage = MonitorStorage(temp_path / "monitor_users.json")
            state = build_default_state()
            state["users"].append(
                {
                    "id": "user-fail",
                    "profile_url": "https://www.douyin.com/user/fail",
                    "sec_user_id": "sec-fail",
                    "nickname": "",
                    "avatar_url": None,
                    "account_status": "normal",
                    "account_status_label": "正常",
                    "account_status_reason": None,
                    "account_status_updated_at": None,
                    "enabled": True,
                    "created_at": "2026-04-13T12:00:00+08:00",
                    "updated_at": "2026-04-13T12:00:00+08:00",
                    "last_checked_at": None,
                    "last_download_at": None,
                    "last_aweme_id": None,
                    "downloaded_count": 0,
                    "downloaded_aweme_ids": [],
                    "download_records": [],
                    "history_sync": {"status": "idle", "has_more": False},
                    "last_error": None,
                }
            )
            storage.save_state(state)
            service = MonitorService(
                crawler=crawler,
                storage=storage,
                download_root=temp_path / "download",
                notifier=NoopMonitorNotifier(),
            )

            users = await service.list_users_with_profile()

            self.assertEqual(len(users), 1)
            self.assertIn("资料补全失败", str(users[0]["last_error"]))
            self.assertEqual(crawler.profile_calls, 1)


if __name__ == "__main__":
    unittest.main()
