import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from douyin_user_monitor.api import monitor as monitor_api
from douyin_user_monitor.main import app
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.service import MonitorService
from douyin_user_monitor.monitor.storage import MonitorStorage, build_default_state
from douyin_user_monitor.monitor.user_sync import UserSyncService


SAMPLE_PROFILE = {
    "user": {
        "nickname": "测试用户",
        "avatar_thumb": {"url_list": ["https://example.com/avatar-thumb.jpeg"]},
        "avatar_medium": {"url_list": ["https://example.com/avatar-medium.jpeg"]},
    }
}


class FakeCrawler:
    async def handler_user_profile(self, sec_user_id: str):
        _ = sec_user_id
        return SAMPLE_PROFILE

    async def get_sec_user_id(self, url: str) -> str:
        return url

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = sec_user_id, max_cursor, count
        return {"aweme_list": []}

    async def fetch_one_video(self, aweme_id: str):
        _ = aweme_id
        return {}

    async def get_douyin_headers(self):
        return {"headers": {}}


class UserSyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_profile_snapshot_extracts_avatar_url(self):
        service = UserSyncService(
            crawler=FakeCrawler(),
            downloader=None,
            notifier=NoopMonitorNotifier(),
        )

        profile = await service.resolve_profile_snapshot("sec_user_id")

        self.assertEqual(profile.nickname, "测试用户")
        self.assertEqual(profile.avatar_url, "https://example.com/avatar-thumb.jpeg")


class MonitorServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_users_with_profile_hydrates_existing_user_avatar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage = MonitorStorage(temp_path / "monitor_users.json")
            state = build_default_state()
            state["users"].append(
                {
                    "id": "user-1",
                    "profile_url": "https://www.douyin.com/user/test",
                    "sec_user_id": "sec_user_id",
                    "nickname": "测试用户",
                    "avatar_url": None,
                    "enabled": True,
                    "created_at": "2026-03-26T00:00:00+08:00",
                    "updated_at": "2026-03-26T00:00:00+08:00",
                    "last_checked_at": None,
                    "last_download_at": None,
                    "last_aweme_id": None,
                    "downloaded_count": 0,
                    "downloaded_aweme_ids": [],
                    "download_records": [],
                    "last_error": None,
                }
            )
            storage.save_state(state)
            service = MonitorService(
                crawler=FakeCrawler(),
                storage=storage,
                download_root=temp_path / "download",
                notifier=NoopMonitorNotifier(),
            )

            users = await service.list_users_with_profile()

            self.assertEqual(users[0]["avatar_url"], "https://example.com/avatar-thumb.jpeg")
            saved_state = storage.load_state()
            self.assertEqual(saved_state["users"][0]["avatar_url"], "https://example.com/avatar-thumb.jpeg")


class FakeAvatarResponse:
    def __init__(self, status_code: int = 200, content_type: str = "image/jpeg", content: bytes = b"avatar-bytes"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.content = content


class FakeAvatarClient:
    def __init__(self, *args, **kwargs):
        _ = args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = exc_type, exc, tb
        return False

    async def get(self, url: str, headers=None):
        _ = headers
        self.last_url = url
        return FakeAvatarResponse()


class AvatarProxyRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_proxy_user_avatar_returns_local_image_response(self):
        users = [{"id": "user-1", "avatar_url": "https://example.com/avatar.jpeg"}]
        with (
            patch.object(monitor_api.monitor_service, "list_users", return_value=users),
            patch("douyin_user_monitor.api.monitor.httpx.AsyncClient", FakeAvatarClient),
        ):
            response = self.client.get("/api/monitor/users/user-1/avatar")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"avatar-bytes")
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertIn("max-age=3600", response.headers["cache-control"])

    def test_proxy_user_avatar_rejects_missing_avatar(self):
        users = [{"id": "user-1", "avatar_url": None}]
        with patch.object(monitor_api.monitor_service, "list_users", return_value=users):
            response = self.client.get("/api/monitor/users/user-1/avatar")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
