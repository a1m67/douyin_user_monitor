"""Shared protection for every process-local Douyin crawler request."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from douyin_user_monitor.services.crawler_circuit_breaker import (
    CrawlerCircuitBreaker,
    classify_crawler_error,
)


T = TypeVar("T")


class DouyinCircuitOpenError(RuntimeError):
    def __init__(self, *, reason: str | None, retry_at: str | None) -> None:
        super().__init__("抖音抓取目前处于全局退避状态")
        self.reason = reason
        self.retry_at = retry_at


class DouyinRequestGuard:
    def __init__(
        self,
        *,
        circuit_breaker: CrawlerCircuitBreaker,
        max_concurrent_requests: int = 3,
        min_request_interval_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_concurrent_requests <= 0:
            raise ValueError("DOUYIN_MAX_CONCURRENT_REQUESTS 必须大于 0")
        if min_request_interval_seconds < 0:
            raise ValueError("DOUYIN_MIN_REQUEST_INTERVAL_SECONDS 不能小于 0")
        self.circuit_breaker = circuit_breaker
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._interval = float(min_request_interval_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        self._rate_lock = asyncio.Lock()
        self._last_started_at: float | None = None
        self._force = ContextVar("douyin_request_force", default=False)

    @asynccontextmanager
    async def force_requests(self):
        token = self._force.set(True)
        try:
            yield
        finally:
            self._force.reset(token)

    async def execute(
        self,
        account_id: str,
        operation: Callable[[], Awaitable[T]],
        *,
        force: bool = False,
    ) -> T:
        use_force = force or self._force.get()
        decision = self.circuit_breaker.before_request(force=use_force)
        if not decision.allowed:
            raise DouyinCircuitOpenError(reason=decision.reason, retry_at=decision.retry_at)
        async with self._semaphore:
            await self._wait_for_rate_slot()
            try:
                result = await operation()
            except Exception as exc:
                self.circuit_breaker.record_failure(account_id, classify_crawler_error(exc))
                raise
            if use_force:
                self.circuit_breaker.reset()
            else:
                self.circuit_breaker.record_success(account_id)
            return result

    async def _wait_for_rate_slot(self) -> None:
        async with self._rate_lock:
            now = self._monotonic()
            if self._last_started_at is not None:
                remaining = self._interval - (now - self._last_started_at)
                if remaining > 0:
                    await self._sleep(remaining)
                    now = self._monotonic()
            self._last_started_at = now

    def snapshot(self) -> dict[str, object]:
        return self.circuit_breaker.snapshot()

    @staticmethod
    def seconds_until_retry(retry_at: str | None) -> float:
        if not retry_at:
            return 1.0
        try:
            value = datetime.fromisoformat(retry_at)
        except ValueError:
            return 1.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(1.0, (value.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
