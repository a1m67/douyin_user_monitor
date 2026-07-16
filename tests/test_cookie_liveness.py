from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from douyin_user_monitor.monitor.cookie_liveness import (
    STATUS_EXPIRED,
    STATUS_HEALTHY,
    STATUS_UNKNOWN,
    CookieLivenessConfig,
    CookieLivenessService,
    evaluate_cookie_liveness,
    extract_latest_create_time,
    select_probe_users,
    should_send_cookie_alert,
)
from douyin_user_monitor.monitor.hermes_weixin_sender import HermesWeixinConfig, HermesWeixinSender
from douyin_user_monitor.monitor.profile_parser import ACCOUNT_STATUS_BANNED, ACCOUNT_STATUS_NORMAL


CHINA_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=CHINA_TZ)
NOW_TS = int(NOW.timestamp())
DAY = 86400


class EvaluateCookieLivenessTests(unittest.TestCase):
    def test_healthy_when_any_sample_is_fresh(self):
        samples = [
            {"latest_create_time": NOW_TS - 10 * DAY, "error": None},
            {"latest_create_time": NOW_TS - 1 * DAY, "error": None},
            {"latest_create_time": NOW_TS - 8 * DAY, "error": None},
        ]
        result = evaluate_cookie_liveness(
            samples,
            now_ts=NOW_TS,
            stale_seconds=7 * DAY,
            min_samples=3,
        )
        self.assertEqual(result.status, STATUS_HEALTHY)

    def test_expired_when_all_samples_are_stale(self):
        samples = [
            {"latest_create_time": NOW_TS - 10 * DAY, "error": None},
            {"latest_create_time": NOW_TS - 8 * DAY, "error": None},
            {"latest_create_time": NOW_TS - 30 * DAY, "error": None},
        ]
        result = evaluate_cookie_liveness(
            samples,
            now_ts=NOW_TS,
            stale_seconds=7 * DAY,
            min_samples=3,
        )
        self.assertEqual(result.status, STATUS_EXPIRED)

    def test_unknown_when_successful_samples_below_min(self):
        samples = [
            {"latest_create_time": NOW_TS - 10 * DAY, "error": None},
            {"latest_create_time": None, "error": "timeout"},
            {"latest_create_time": None, "error": "timeout"},
        ]
        result = evaluate_cookie_liveness(
            samples,
            now_ts=NOW_TS,
            stale_seconds=7 * DAY,
            min_samples=3,
        )
        self.assertEqual(result.status, STATUS_UNKNOWN)

    def test_extract_latest_create_time(self):
        value = extract_latest_create_time(
            [
                {"create_time": 100},
                {"create_time": "200"},
                {"create_time": 150},
            ]
        )
        self.assertEqual(value, 200)


class SelectProbeUsersTests(unittest.TestCase):
    def test_skips_disabled_and_banned(self):
        users = [
            {"id": "b", "enabled": True, "account_status": ACCOUNT_STATUS_NORMAL, "sec_user_id": "sec-b"},
            {"id": "a", "enabled": False, "account_status": ACCOUNT_STATUS_NORMAL, "sec_user_id": "sec-a"},
            {"id": "c", "enabled": True, "account_status": ACCOUNT_STATUS_BANNED, "sec_user_id": "sec-c"},
            {"id": "d", "enabled": True, "account_status": ACCOUNT_STATUS_NORMAL, "sec_user_id": "sec-d"},
        ]
        selected = select_probe_users(users, limit=5)
        self.assertEqual([item["id"] for item in selected], ["b", "d"])


class AlertCooldownTests(unittest.TestCase):
    def test_alerts_on_transition_to_expired(self):
        self.assertTrue(
            should_send_cookie_alert(
                status=STATUS_EXPIRED,
                previous_status=STATUS_HEALTHY,
                last_alert_at=None,
                now=NOW,
                cooldown_hours=12,
            )
        )

    def test_skips_within_cooldown(self):
        last = (NOW - timedelta(hours=1)).isoformat()
        self.assertFalse(
            should_send_cookie_alert(
                status=STATUS_EXPIRED,
                previous_status=STATUS_EXPIRED,
                last_alert_at=last,
                now=NOW,
                cooldown_hours=12,
            )
        )

    def test_allows_after_cooldown(self):
        last = (NOW - timedelta(hours=13)).isoformat()
        self.assertTrue(
            should_send_cookie_alert(
                status=STATUS_EXPIRED,
                previous_status=STATUS_EXPIRED,
                last_alert_at=last,
                now=NOW,
                cooldown_hours=12,
            )
        )


class FakeCrawler:
    def __init__(self, mapping: Dict[str, Any]):
        self.mapping = mapping

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        _ = max_cursor, count
        value = self.mapping[sec_user_id]
        if isinstance(value, Exception):
            raise value
        return value


class RecordingAlerter:
    def __init__(self):
        self.messages: List[str] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)


class CookieLivenessServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_maybe_run_marks_expired_and_alerts(self):
        crawler = FakeCrawler(
            {
                "sec-1": {"aweme_list": [{"create_time": NOW_TS - 10 * DAY}]},
                "sec-2": {"aweme_list": [{"create_time": NOW_TS - 9 * DAY}]},
                "sec-3": {"aweme_list": [{"create_time": NOW_TS - 20 * DAY}]},
            }
        )
        alerter = RecordingAlerter()
        service = CookieLivenessService(
            crawler=crawler,
            config=CookieLivenessConfig(
                enabled=True,
                interval_hours=6,
                stale_days=7,
                sample_user_count=5,
                min_samples=3,
                alert_cooldown_hours=12,
            ),
            alerter=alerter,
            now_provider=lambda: NOW,
        )
        state = {
            "users": [
                {
                    "id": f"u{i}",
                    "nickname": f"n{i}",
                    "enabled": True,
                    "account_status": ACCOUNT_STATUS_NORMAL,
                    "sec_user_id": f"sec-{i}",
                }
                for i in range(1, 4)
            ],
            "monitoring": {},
        }

        payload = await service.maybe_run(state)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["status"], STATUS_EXPIRED)
        self.assertTrue(payload["alerted"])
        self.assertEqual(len(alerter.messages), 1)
        self.assertIn("Cookie 疑似失效", alerter.messages[0])

        # second run within interval should skip
        payload2 = await service.maybe_run(state)
        self.assertIsNone(payload2)
        self.assertEqual(len(alerter.messages), 1)


class RecordingRunner:
    def __init__(self, code: int = 0):
        self.code = code
        self.commands: List[List[str]] = []

    async def run(self, command, *, timeout_seconds: float):
        _ = timeout_seconds
        self.commands.append(list(command))
        return self.code, "ok", ""


class HermesWeixinSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_ssh_hermes_command(self):
        runner = RecordingRunner(code=0)
        sender = HermesWeixinSender(
            HermesWeixinConfig(
                enabled=True,
                ssh_host="hermes.example.test",
                ssh_user="root",
                hermes_home="/opt/hermes",
                hermes_bin="/opt/hermes/bin/hermes",
                target="weixin",
                timeout_seconds=30,
            ),
            runner=runner,
        )
        await sender.send("hello world")
        self.assertEqual(len(runner.commands), 1)
        cmd = runner.commands[0]
        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[-2], "root@hermes.example.test")
        self.assertIn("hermes", cmd[-1])
        self.assertIn("hello world", cmd[-1])

    async def test_raises_on_nonzero_exit(self):
        runner = RecordingRunner(code=1)
        sender = HermesWeixinSender(
            HermesWeixinConfig(
                enabled=True,
                ssh_host="hermes.example.test",
                ssh_user="root",
                hermes_home="/opt/hermes",
                hermes_bin="/bin/hermes",
                target="weixin",
                timeout_seconds=30,
            ),
            runner=runner,
        )
        with self.assertRaises(RuntimeError):
            await sender.send("fail")


if __name__ == "__main__":
    unittest.main()
