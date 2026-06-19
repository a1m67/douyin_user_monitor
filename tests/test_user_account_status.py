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

BANNED_VIA_STATUS_MSG = {
    "status_code": 0,
    "status_msg": "该账号因违规已封禁",
    "user": {
        "nickname": "违规账号",
        "special_state_info": {
            "title": "",
            "content": "",
        },
    },
}

BANNED_VIA_CONTENT = {
    "status_code": 0,
    "status_msg": "",
    "user": {
        "nickname": "内容封禁",
        "special_state_info": {
            "title": "",
            "content": "该账号已被封禁",
        },
    },
}

NON_BANNED_STATUS_MSG_PROFILE = {
    "status_code": 0,
    "status_msg": "封禁申诉入口已开放",
    "user": {
        "nickname": "申诉用户",
        "special_state_info": {
            "title": "帮助中心",
            "content": "如需申诉封禁，请点击此处",
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


class BannedAccountCrawler:
    def __init__(self):
        self.profile_calls = 0
        self.fetch_posts_calls = 0

    async def handler_user_profile(self, sec_user_id: str):
        _ = sec_user_id
        self.profile_calls += 1
        return BANNED_PROFILE

    async def get_sec_user_id(self, url: str) -> str:
        return url

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id, max_cursor, count
        self.fetch_posts_calls += 1
        return {"aweme_list": None, "has_more": 0, "max_cursor": max_cursor}

    async def fetch_one_video(self, aweme_id: str):
        _ = aweme_id
        return {}

    async def get_douyin_headers(self):
        return {"headers": {}}


class TrackingDeletedCrawler:
    """Tracks whether fetch_user_post_videos is called."""
    def __init__(self):
        self.profile_calls = 0
        self.fetch_posts_calls = 0

    async def handler_user_profile(self, sec_user_id: str):
        _ = sec_user_id
        self.profile_calls += 1
        return DELETED_PROFILE

    async def get_sec_user_id(self, url: str) -> str:
        return url

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id, max_cursor, count
        self.fetch_posts_calls += 1
        return {"aweme_list": None, "has_more": 0, "max_cursor": max_cursor}

    async def fetch_one_video(self, aweme_id: str):
        _ = aweme_id
        return {}

    async def get_douyin_headers(self):
        return {"headers": {}}


class StatusTrackingNotifier:
    """Tracks status change notifications."""
    def __init__(self):
        self.status_changes: list[dict] = []

    async def notify_new_aweme_detected(self, *, user_nickname: str, aweme_detail):
        pass

    async def notify_download_completed(self, *, user_nickname: str, record):
        pass

    async def notify_account_status_changed(
        self,
        *,
        user_nickname: str,
        old_status: str,
        new_status: str,
        reason: str | None,
    ) -> None:
        self.status_changes.append({
            "user_nickname": user_nickname,
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        })


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

    def test_extract_account_status_detects_banned_via_status_msg(self):
        status = extract_account_status(BANNED_VIA_STATUS_MSG)

        self.assertEqual(status["account_status"], "banned")
        self.assertEqual(status["account_status_label"], "已封禁")
        self.assertIn("违规已封禁", str(status["account_status_reason"]))

    def test_extract_account_status_detects_banned_via_content(self):
        status = extract_account_status(BANNED_VIA_CONTENT)

        self.assertEqual(status["account_status"], "banned")
        self.assertEqual(status["account_status_label"], "已封禁")
        self.assertIn("已被封禁", str(status["account_status_reason"]))

    def test_extract_account_status_does_not_guess_banned_from_status_msg(self):
        status = extract_account_status(NON_BANNED_STATUS_MSG_PROFILE)

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


class UserSyncSkipFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_skips_post_fetch_for_deleted_user(self):
        crawler = TrackingDeletedCrawler()
        service = UserSyncService(
            crawler=crawler,
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-del",
            "sec_user_id": "sec-del",
            "nickname": "已注销",
            "avatar_url": None,
            "account_status": "deleted",
            "account_status_label": "已注销",
            "account_status_reason": "账号已注销",
            "account_status_updated_at": "2026-01-01T00:00:00Z",
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": {"status": "idle", "has_more": False},
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(crawler.fetch_posts_calls, 0)
        self.assertEqual(summary["errors"], [])

    async def test_sync_skips_post_fetch_for_banned_user(self):
        crawler = BannedAccountCrawler()
        service = UserSyncService(
            crawler=crawler,
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-ban",
            "sec_user_id": "sec-ban",
            "nickname": "已封禁",
            "avatar_url": None,
            "account_status": "normal",
            "account_status_label": "正常",
            "account_status_reason": None,
            "account_status_updated_at": None,
            "enabled": True,
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": {"status": "idle", "has_more": False},
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        # After sync, account_status becomes "banned" from profile snapshot,
        # so _sync_user_latest should skip post fetching
        self.assertEqual(user["account_status"], "banned")
        self.assertEqual(crawler.fetch_posts_calls, 0)
        # Auto-pause monitoring
        self.assertFalse(user["enabled"])


class UserSyncNicknamePreservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_existing_nickname_when_api_returns_empty(self):
        """Deleted/banned users should keep their original nickname, not sec_user_id[:12]."""
        service = UserSyncService(
            crawler=DeletedAccountCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-user-12345",
            "nickname": "原昵称",
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

        self.assertEqual(user["nickname"], "原昵称")
        self.assertEqual(user["account_status"], "deleted")

    async def test_uses_fallback_nickname_when_no_existing_nickname(self):
        """New users added as deleted should still get sec_user_id[:12] as nickname."""
        service = UserSyncService(
            crawler=DeletedAccountCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )
        user = {
            "id": "user-2",
            "sec_user_id": "sec-user-12345",
            "nickname": "",
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

        self.assertEqual(user["nickname"], "sec-user-123")
        self.assertEqual(user["account_status"], "deleted")


class UserSyncNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_on_normal_to_deleted_transition(self):
        notifier = StatusTrackingNotifier()
        service = UserSyncService(
            crawler=DeletedAccountCrawler(),
            downloader=None,
            notifier=notifier,
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "测试用户",
            "avatar_url": None,
            "account_status": "normal",
            "account_status_label": "正常",
            "account_status_reason": None,
            "account_status_updated_at": None,
            "enabled": True,
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": {"status": "idle", "has_more": False},
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(len(notifier.status_changes), 1)
        change = notifier.status_changes[0]
        # Nickname preserved from existing user data (not overwritten by sec_user_id[:12])
        self.assertEqual(change["user_nickname"], "测试用户")
        self.assertEqual(change["old_status"], "normal")
        self.assertEqual(change["new_status"], "deleted")
        self.assertIsNotNone(change["reason"])
        # Auto-pause monitoring on status change
        self.assertFalse(user["enabled"])

    async def test_no_notification_when_already_deleted(self):
        notifier = StatusTrackingNotifier()
        service = UserSyncService(
            crawler=DeletedAccountCrawler(),
            downloader=None,
            notifier=notifier,
        )
        user = {
            "id": "user-1",
            "sec_user_id": "sec-1",
            "nickname": "已注销用户",
            "avatar_url": None,
            "account_status": "deleted",
            "account_status_label": "已注销",
            "account_status_reason": "已注销",
            "account_status_updated_at": "2026-01-01T00:00:00Z",
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "last_aweme_id": None,
            "history_sync": {"status": "idle", "has_more": False},
        }
        summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}

        await service.sync_one_user(user, summary)

        self.assertEqual(len(notifier.status_changes), 0)


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
