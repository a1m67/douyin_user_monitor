"""Account-level staggered scheduler with bounded concurrency and backoff."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.crawler_circuit_breaker import (
    CrawlerCircuitBreaker,
    classify_crawler_error,
)
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline, SyncResult
from douyin_user_monitor.services.douyin_request_guard import (
    DouyinCircuitOpenError,
    DouyinRequestGuard,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerConfig:
    default_check_interval_minutes: int = 10
    max_concurrent_checks: int = 3
    max_backoff_minutes: int = 60
    jitter_ratio: float = 0.1
    poll_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.default_check_interval_minutes <= 0:
            raise ValueError("default_check_interval_minutes 必须大于 0")
        if self.max_concurrent_checks <= 0:
            raise ValueError("max_concurrent_checks 必须大于 0")
        if self.max_backoff_minutes <= 0:
            raise ValueError("max_backoff_minutes 必须大于 0")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio 必须在 0 到 1 之间")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds 必须大于 0")


@dataclass(frozen=True)
class AccountCheckResult:
    account_id: str
    success: bool
    next_check_at: str
    sync_result: SyncResult | None = None
    error: str | None = None
    error_type: str | None = None
    circuit_open: bool = False


class AccountScheduler:
    def __init__(
        self,
        *,
        repository: ShortDramaRepository,
        pipeline: ShortDramaPipeline,
        config: SchedulerConfig,
        now: Callable[[], datetime] | None = None,
        jitter: Callable[[float, float], float] | None = None,
        circuit_breaker: CrawlerCircuitBreaker | None = None,
        request_guard: DouyinRequestGuard | None = None,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._config = config
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._jitter = jitter or random.uniform
        self._circuit_breaker = circuit_breaker
        self._request_guard = request_guard
        self._task: asyncio.Task[None] | None = None
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._account_lock_users: dict[str, int] = {}
        self._account_locks_guard = asyncio.Lock()

    async def _account_lock(self, account_id: str) -> asyncio.Lock:
        async with self._account_locks_guard:
            key = str(account_id)
            self._account_lock_users[key] = self._account_lock_users.get(key, 0) + 1
            return self._account_locks.setdefault(key, asyncio.Lock())

    async def _run_one_account(self, account_id: str, *, force: bool, trigger_type: str) -> AccountCheckResult:
        lock = await self._account_lock(account_id)
        try:
            async with lock:
                return await self._check_account(account_id, force=force, trigger_type=trigger_type)
        finally:
            async with self._account_locks_guard:
                key = str(account_id)
                remaining = self._account_lock_users.get(key, 1) - 1
                if remaining <= 0:
                    self._account_lock_users.pop(key, None)
                    self._account_locks.pop(key, None)
                else:
                    self._account_lock_users[key] = remaining

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="short-drama-account-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        task, self._task = self._task, None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def health_status(self) -> str:
        return "ok" if self._task is not None and not self._task.done() else "stopped"

    async def run_due_once(self) -> tuple[AccountCheckResult, ...]:
        now = _as_utc(self._now())
        accounts = self._repository.due_accounts(now=now.isoformat(timespec="seconds"), limit=500)
        if not accounts:
            return ()
        semaphore = asyncio.Semaphore(self._config.max_concurrent_checks)

        async def check(account_id: str) -> AccountCheckResult:
            async with semaphore:
                return await self._run_one_account(account_id, force=False, trigger_type="scheduler")

        return tuple(await asyncio.gather(*(check(str(account["id"])) for account in accounts)))

    async def run_account_once(self, account_id: str, *, force: bool = False) -> AccountCheckResult:
        return await self._run_one_account(account_id, force=force, trigger_type="manual")

    def crawler_status(self) -> dict[str, object]:
        if self._request_guard is not None:
            return self._request_guard.snapshot()
        if self._circuit_breaker is None:
            return {"enabled": False, "state": "closed", "reason": None, "retry_at": None}
        return self._circuit_breaker.snapshot()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Per-account errors are handled in _check_account. A defensive
                # loop guard keeps unrelated accounts schedulable after bugs.
                pass
            await asyncio.sleep(self._config.poll_seconds)

    async def _check_account(self, account_id: str, *, force: bool = False, trigger_type: str = "scheduler") -> AccountCheckResult:
        account = self._repository.get_account(account_id)
        if account is None:
            raise KeyError("账号不存在")
        now = _as_utc(self._now())
        started_at, started_clock = now.isoformat(timespec="seconds"), time.monotonic()
        if self._circuit_breaker is not None:
            decision = self._circuit_breaker.before_request(force=force)
            if not decision.allowed:
                return AccountCheckResult(
                    account_id=account_id,
                    success=False,
                    next_check_at=decision.retry_at or now.isoformat(timespec="seconds"),
                    error="抖音抓取目前处于全局退避状态",
                    error_type=decision.reason,
                    circuit_open=True,
                )
        try:
            if force and self._request_guard is not None:
                async with self._request_guard.force_requests():
                    sync_result = await self._pipeline.sync_account(account_id)
            else:
                sync_result = await self._pipeline.sync_account(account_id)
        except Exception as exc:  # noqa: BLE001 - crawler/provider failure is an account failure
            error_type = classify_crawler_error(exc)
            circuit_open = isinstance(exc, DouyinCircuitOpenError)
            if circuit_open:
                return AccountCheckResult(
                    account_id=account_id,
                    success=False,
                    next_check_at=exc.retry_at or now.isoformat(timespec="seconds"),
                    error=str(exc),
                    error_type=exc.reason or "circuit_open",
                    circuit_open=True,
                )
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure(account_id, error_type)
            backoff_minutes = calculate_backoff_minutes(
                interval_minutes=int(account["check_interval_minutes"]),
                consecutive_failures=int(account["consecutive_failures"]),
                max_backoff_minutes=self._config.max_backoff_minutes,
            )
            next_check_at = now + timedelta(minutes=backoff_minutes)
            stored = self._repository.mark_account_sync_failure(
                account_id,
                error=_safe_error(exc),
                next_check_at=next_check_at.isoformat(timespec="seconds"),
            )
            logger.warning(
                "[account] failed account_id=%s reason=%s retry_after=%sm",
                account_id,
                _safe_error(exc),
                backoff_minutes,
            )
            self._record_scan(account_id, started_at, started_clock, trigger_type, False, error_type=error_type, error_message=_safe_error(exc))
            return AccountCheckResult(
                account_id=account_id,
                success=False,
                next_check_at=str(stored["next_check_at"]),
                error=_safe_error(exc),
                error_type=error_type,
                circuit_open=circuit_open,
            )

        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success(account_id)
        next_check_at = calculate_next_check_at(
            now=now,
            interval_minutes=int(account["check_interval_minutes"]),
            jitter_ratio=self._config.jitter_ratio,
            jitter=self._jitter,
        )
        stored = self._repository.mark_account_sync_success(
            account_id,
            next_check_at=next_check_at.isoformat(timespec="seconds"),
        )
        actual_trigger = "initial_sync" if getattr(sync_result, "initial_sync", False) else trigger_type
        self._record_scan(account_id, started_at, started_clock, actual_trigger, True, result=sync_result)
        logger.info("[account] complete account_id=%s next_check_at=%s", account_id, stored["next_check_at"])
        return AccountCheckResult(
            account_id=account_id,
            success=True,
            next_check_at=str(stored["next_check_at"]),
            sync_result=sync_result,
        )

    def _record_scan(self, account_id: str, started_at: str, started_clock: float, trigger_type: str, success: bool, *, result: SyncResult | None = None, error_type: str | None = None, error_message: str | None = None) -> None:
        try:
            self._repository.record_scan_run(
                account_id, started_at=started_at, finished_at=_as_utc(self._now()).isoformat(timespec="seconds"),
                duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)), trigger_type=trigger_type,
                success=int(success), error_type=error_type, error_message=error_message,
                fetched_videos=getattr(result, "fetched_videos", 0), new_videos=getattr(result, "new_videos", 0),
                duplicate_videos=getattr(result, "duplicate_videos", 0), matched_videos=getattr(result, "matched_videos", 0),
                review_videos=getattr(result, "review_videos", 0), ignored_videos=getattr(result, "ignored_videos", 0),
                new_episodes=len(getattr(result, "new_episode_updates", ())), llm_calls=getattr(result, "llm_calls", 0),
                regex_calls=getattr(result, "regex_calls", 0), context_calls=getattr(result, "context_calls", 0),
                ocr_calls=getattr(result, "ocr_calls", 0), ocr_successes=getattr(result, "ocr_successes", 0),
                llm_latency_ms_total=getattr(result, "llm_latency_ms_total", 0),
                ocr_latency_ms_total=getattr(result, "ocr_latency_ms_total", 0),
            )
        except Exception:
            logger.exception("scan run persistence failed account_id=%s", account_id)


def calculate_next_check_at(
    *,
    now: datetime,
    interval_minutes: int,
    jitter_ratio: float,
    jitter: Callable[[float, float], float] = random.uniform,
) -> datetime:
    base_seconds = max(1, interval_minutes) * 60.0
    jitter_seconds = base_seconds * jitter_ratio
    delay_seconds = max(1.0, base_seconds + jitter(-jitter_seconds, jitter_seconds))
    return _as_utc(now) + timedelta(seconds=delay_seconds)


def calculate_backoff_minutes(
    *,
    interval_minutes: int,
    consecutive_failures: int,
    max_backoff_minutes: int,
) -> int:
    failures_after_current = max(1, consecutive_failures + 1)
    delay = max(1, interval_minutes) * (2 ** (failures_after_current - 1))
    return min(delay, max_backoff_minutes)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return re.sub(
        r"(?i)\b(cookie|token|authorization|webhook)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2***",
        text,
    )[:2000]
