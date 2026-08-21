from __future__ import annotations

import asyncio
import unittest

from douyin_user_monitor.services.crawler_circuit_breaker import CrawlerCircuitBreaker
from douyin_user_monitor.services.douyin_request_guard import (
    DouyinCircuitOpenError,
    DouyinRequestGuard,
)


class DouyinRequestGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_distinct_failures_open_shared_circuit_and_force_success_resets(self):
        breaker = CrawlerCircuitBreaker(failure_threshold=3, open_minutes=20)
        guard = DouyinRequestGuard(
            circuit_breaker=breaker,
            max_concurrent_requests=3,
            min_request_interval_seconds=0,
        )

        async def fail():
            raise RuntimeError("login_required")

        for account_id in ("one", "two", "three"):
            with self.assertRaises(RuntimeError):
                await guard.execute(account_id, fail)
        self.assertEqual(guard.snapshot()["state"], "open")
        with self.assertRaises(DouyinCircuitOpenError):
            await guard.execute("history", lambda: asyncio.sleep(0))

        async with guard.force_requests():
            self.assertEqual(await guard.execute("cookie-test", lambda: _value("ok")), "ok")
        self.assertEqual(guard.snapshot()["state"], "closed")

    async def test_global_concurrency_and_minimum_interval_are_enforced(self):
        guard = DouyinRequestGuard(
            circuit_breaker=CrawlerCircuitBreaker(),
            max_concurrent_requests=2,
            min_request_interval_seconds=0.02,
        )
        active = 0
        peak = 0
        started: list[float] = []
        loop = asyncio.get_running_loop()

        async def request():
            nonlocal active, peak
            started.append(loop.time())
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.04)
            finally:
                active -= 1

        await asyncio.gather(*(guard.execute(str(index), request) for index in range(4)))
        self.assertLessEqual(peak, 2)
        self.assertTrue(all(right - left >= 0.015 for left, right in zip(started, started[1:])))


async def _value(value):
    return value


if __name__ == "__main__":
    unittest.main()
