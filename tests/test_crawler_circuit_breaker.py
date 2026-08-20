from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from douyin_user_monitor.services.crawler_circuit_breaker import (
    CrawlerCircuitBreaker,
    classify_crawler_error,
)


class CrawlerCircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        self.breaker = CrawlerCircuitBreaker(
            failure_threshold=3,
            open_minutes=20,
            now=lambda: self.now,
        )

    def test_three_distinct_accounts_open_but_one_account_does_not(self):
        for _ in range(4):
            self.breaker.record_failure("same", "http_403")
        self.assertEqual(self.breaker.snapshot()["state"], "closed")
        self.breaker.record_failure("second", "http_403")
        self.breaker.record_failure("third", "http_403")
        self.assertEqual(self.breaker.snapshot()["state"], "open")
        self.assertEqual(self.breaker.snapshot()["reason"], "http_403")
        self.assertFalse(self.breaker.before_request().allowed)

    def test_open_expires_to_single_half_open_probe_and_success_closes(self):
        for account_id in ("one", "two", "three"):
            self.breaker.record_failure(account_id, "login_required")
        self.now += timedelta(minutes=20)
        probe = self.breaker.before_request()
        blocked = self.breaker.before_request()
        self.assertTrue(probe.allowed)
        self.assertEqual(probe.state, "half_open")
        self.assertFalse(blocked.allowed)
        self.breaker.record_success("probe")
        self.assertEqual(self.breaker.snapshot()["state"], "closed")

    def test_half_open_failure_reopens(self):
        for account_id in ("one", "two", "three"):
            self.breaker.record_failure(account_id, "http_429")
        self.now += timedelta(minutes=20)
        self.assertTrue(self.breaker.before_request().allowed)
        self.breaker.record_failure("probe", "timeout")
        self.assertEqual(self.breaker.snapshot()["state"], "open")
        self.assertEqual(self.breaker.snapshot()["reason"], "timeout")

    def test_error_classification(self):
        cases = {
            "HTTP status 403 Forbidden": "http_403",
            "429 too many requests": "http_429",
            "cookie expired": "cookie_invalid",
            "login_required": "login_required",
            "risk control captcha": "risk_control",
            "request timed out": "timeout",
            "network connection failed": "network",
            "empty_response": "empty_response",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(classify_crawler_error(RuntimeError(message)), expected)


if __name__ == "__main__":
    unittest.main()
