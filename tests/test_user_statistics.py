import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from douyin_user_monitor.api import monitor as monitor_api
from douyin_user_monitor.main import app
from douyin_user_monitor.monitor.statistics import build_user_statistics


CHINA_TZ = timezone(timedelta(hours=8))


def build_record(publish_time: str, media_type: str, image_count: int = 0, total_size_bytes: int = 0):
    return {
        "aweme_id": f"aweme-{publish_time}-{media_type}",
        "publish_time": publish_time,
        "downloaded_at": publish_time,
        "media_type": media_type,
        "image_count": image_count,
        "total_size_bytes": total_size_bytes,
    }


class BuildUserStatisticsTests(unittest.TestCase):
    def test_build_user_statistics_aggregates_summary_and_silent_users(self):
        now = datetime(2026, 3, 27, 12, 0, tzinfo=CHINA_TZ)
        users = [
            {
                "id": "user-1",
                "nickname": "活跃用户",
                "sec_user_id": "sec-1",
                "enabled": True,
                "account_status": "normal",
                "account_status_label": "正常",
                "account_status_reason": None,
                "account_status_updated_at": "2026-03-27T09:00:00+08:00",
                "downloaded_count": 5,
                "downloaded_aweme_ids": ["1", "2", "3", "4", "5"],
                "download_records": [
                    build_record("2026-03-26T10:00:00+08:00", "video", total_size_bytes=100),
                    build_record("2026-03-24T08:00:00+08:00", "video", total_size_bytes=120),
                    build_record("2026-03-20T18:00:00+08:00", "image", image_count=3, total_size_bytes=180),
                ],
            },
            {
                "id": "user-2",
                "nickname": "沉默用户",
                "sec_user_id": "sec-2",
                "enabled": True,
                "account_status": "deleted",
                "account_status_label": "已注销",
                "account_status_reason": "账号已经注销",
                "account_status_updated_at": "2026-03-27T10:00:00+08:00",
                "downloaded_count": 2,
                "downloaded_aweme_ids": ["6", "7"],
                "download_records": [
                    build_record("2026-03-10T09:00:00+08:00", "image", image_count=5, total_size_bytes=200),
                ],
            },
            {
                "id": "user-3",
                "nickname": "无记录用户",
                "sec_user_id": "sec-3",
                "enabled": False,
                "account_status": "banned",
                "account_status_label": "已封禁",
                "account_status_reason": "因违规被封禁",
                "account_status_updated_at": "2026-03-27T11:00:00+08:00",
                "downloaded_count": 0,
                "downloaded_aweme_ids": [],
                "download_records": [],
            },
        ]

        statistics = build_user_statistics(users, now=now)
        summary = statistics["summary"]

        self.assertEqual(summary["total_users"], 3)
        self.assertEqual(summary["enabled_users"], 2)
        self.assertEqual(summary["paused_users"], 1)
        self.assertEqual(summary["total_downloaded_works"], 7)
        self.assertEqual(summary["structured_work_count"], 4)
        self.assertEqual(summary["known_video_posts"], 2)
        self.assertEqual(summary["known_image_posts"], 2)
        self.assertEqual(summary["known_image_assets"], 8)
        self.assertEqual(summary["silent_users_7d"], 1)
        self.assertEqual(summary["silent_users_30d"], 0)
        self.assertEqual(summary["unknown_publish_users"], 1)
        self.assertEqual(summary["abnormal_users"], 2)
        self.assertEqual(summary["deleted_users"], 1)
        self.assertEqual(summary["banned_users"], 1)
        self.assertAlmostEqual(summary["structured_coverage_percent"], 57.1, places=1)
        self.assertEqual(statistics["lists"]["silent_users_7d"][0]["nickname"], "沉默用户")
        self.assertEqual(statistics["lists"]["unknown_publish_users"][0]["nickname"], "无记录用户")
        self.assertEqual(statistics["lists"]["deactivated_users"][0]["nickname"], "沉默用户")
        self.assertEqual(statistics["lists"]["deactivated_users"][1]["nickname"], "无记录用户")
        self.assertEqual(statistics["rankings"]["top_downloaded_users"][0]["nickname"], "活跃用户")


class UserStatisticsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_statistics_route_returns_aggregated_payload(self):
        users = [
            {
                "id": "user-1",
                "nickname": "统计测试",
                "sec_user_id": "sec-1",
                "enabled": True,
                "account_status": "normal",
                "account_status_label": "正常",
                "account_status_reason": None,
                "account_status_updated_at": "2026-03-26T11:00:00+08:00",
                "downloaded_count": 3,
                "downloaded_aweme_ids": ["1", "2", "3"],
                "download_records": [
                    build_record("2026-03-26T10:00:00+08:00", "video", total_size_bytes=100),
                ],
            }
        ]
        with patch.object(
            monitor_api.monitor_service,
            "list_users_with_profile",
            AsyncMock(return_value=users),
        ):
            response = self.client.get("/api/monitor/statistics")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 200)
        self.assertEqual(body["data"]["summary"]["total_users"], 1)
        self.assertEqual(body["data"]["summary"]["total_downloaded_works"], 3)
        self.assertEqual(body["data"]["rankings"]["top_downloaded_users"][0]["nickname"], "统计测试")


if __name__ == "__main__":
    unittest.main()
