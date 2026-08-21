"""Independent cost and failure guards for optional AI services."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, TypeVar

from douyin_user_monitor.ocr import OCRBackend, OCRResult
from douyin_user_monitor.parsers.base import EpisodeParseInput, EpisodeParseResult
from douyin_user_monitor.parsers.llm import LLMParser, llm_failure_result
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository


T = TypeVar("T")


class AIRequestUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Reservation:
    usage_date: str
    started_at: float


class AIRequestGuard:
    """Bound one provider without coupling it to the Douyin crawler guard."""

    def __init__(
        self,
        repository: ShortDramaRepository,
        *,
        provider: str,
        max_concurrent_requests: int,
        daily_call_limit: int = 0,
        failure_threshold: int = 5,
        cooldown_minutes: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_concurrent_requests <= 0:
            raise ValueError("AI 最大并发必须大于 0")
        if daily_call_limit < 0:
            raise ValueError("AI 每日调用额度不能小于 0")
        if failure_threshold <= 0 or cooldown_minutes <= 0:
            raise ValueError("AI 熔断阈值和冷却时间必须大于 0")
        self.repository = repository
        self.provider = str(provider).strip().lower()
        if not self.provider:
            raise ValueError("AI provider 不能为空")
        self.max_concurrent_requests = int(max_concurrent_requests)
        self.daily_call_limit = int(daily_call_limit)
        self.failure_threshold = int(failure_threshold)
        self.cooldown_minutes = int(cooldown_minutes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent_requests)
        self._state_lock = threading.Lock()
        self._consecutive_failures = 0
        self._cooldown_until: datetime | None = None

    def call(self, callback: Callable[[], T], *, is_success: Callable[[T], bool] | None = None) -> T:
        reservation = self._begin()
        try:
            result = callback()
        except BaseException:
            self._finish(reservation, success=False)
            raise
        self._finish(reservation, success=True if is_success is None else bool(is_success(result)))
        return result

    async def call_async(
        self,
        callback: Callable[[], Awaitable[T]],
        *,
        is_success: Callable[[T], bool] | None = None,
    ) -> T:
        reservation = self._begin()
        try:
            result = await callback()
        except BaseException:
            self._finish(reservation, success=False)
            raise
        self._finish(reservation, success=True if is_success is None else bool(is_success(result)))
        return result

    def status(self) -> dict[str, Any]:
        now = self._utc_now()
        with self._state_lock:
            self._reset_expired_cooldown(now)
            cooldown_until = self._cooldown_until
            failures = self._consecutive_failures
        return {
            "status": "cooldown" if cooldown_until is not None else "healthy",
            "consecutive_failures": failures,
            "cooldown_until": cooldown_until.isoformat(timespec="seconds") if cooldown_until else None,
            "max_concurrent_requests": self.max_concurrent_requests,
            "daily_call_limit": self.daily_call_limit,
        }

    def _begin(self) -> _Reservation:
        self._ensure_circuit_closed()
        self._semaphore.acquire()
        try:
            self._ensure_circuit_closed()
            usage_date = self._utc_now().date().isoformat()
            if not self.repository.reserve_ai_request(
                provider=self.provider,
                usage_date=usage_date,
                daily_call_limit=self.daily_call_limit,
            ):
                raise AIRequestUnavailable(f"{self.provider}_budget_exhausted")
            return _Reservation(usage_date=usage_date, started_at=time.perf_counter())
        except BaseException:
            self._semaphore.release()
            raise

    def _finish(self, reservation: _Reservation, *, success: bool) -> None:
        latency_ms = max(0, int((time.perf_counter() - reservation.started_at) * 1000))
        try:
            self.repository.complete_ai_request(
                provider=self.provider,
                usage_date=reservation.usage_date,
                success=success,
                latency_ms=latency_ms,
            )
            now = self._utc_now()
            with self._state_lock:
                self._reset_expired_cooldown(now)
                if success:
                    self._consecutive_failures = 0
                    self._cooldown_until = None
                else:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self.failure_threshold:
                        self._cooldown_until = now + timedelta(minutes=self.cooldown_minutes)
        finally:
            self._semaphore.release()

    def _ensure_circuit_closed(self) -> None:
        now = self._utc_now()
        with self._state_lock:
            self._reset_expired_cooldown(now)
            if self._cooldown_until is not None:
                raise AIRequestUnavailable(f"{self.provider}_circuit_open")

    def _reset_expired_cooldown(self, now: datetime) -> None:
        if self._cooldown_until is not None and now >= self._cooldown_until:
            self._cooldown_until = None
            self._consecutive_failures = 0

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class GuardedLLMParser:
    def __init__(self, backend: LLMParser, guard: AIRequestGuard) -> None:
        self._backend = backend
        self._guard = guard

    def parse(self, request: EpisodeParseInput, regex_result: EpisodeParseResult) -> EpisodeParseResult:
        try:
            return self._guard.call(
                lambda: self._backend.parse(request, regex_result),
                is_success=lambda result: result.reason not in {
                    "llm_timeout",
                    "llm_http_error",
                    "llm_invalid_response",
                    "llm_call_failed",
                },
            )
        except AIRequestUnavailable as exc:
            return llm_failure_result(regex_result, exc.reason)


class GuardedOCRBackend:
    def __init__(self, backend: OCRBackend, guard: AIRequestGuard) -> None:
        self._backend = backend
        self._guard = guard

    async def extract_text(self, image_url: str) -> OCRResult:
        return await self._guard.call_async(lambda: self._backend.extract_text(image_url))
