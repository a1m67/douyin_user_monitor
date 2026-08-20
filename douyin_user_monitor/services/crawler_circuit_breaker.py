"""Process-wide circuit breaker for systemic Douyin crawler failures."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


GLOBAL_ERROR_TYPES = frozenset(
    {"login_required", "cookie_invalid", "risk_control", "http_403", "http_429"}
)


def classify_crawler_error(exc: Exception) -> str:
    text = f"{exc.__class__.__name__} {exc}".casefold()
    rules = (
        ("login_required", r"login[_ ]?required|请登录|登录失效|未登录"),
        ("cookie_invalid", r"cookie.{0,24}(?:invalid|expired|失效|过期)"),
        ("risk_control", r"risk[_ -]?control|风控|captcha|验证码"),
        ("http_403", r"(?:status|http|code)[^\d]{0,8}403|\b403\b|forbidden"),
        ("http_429", r"(?:status|http|code)[^\d]{0,8}429|\b429\b|too many requests"),
        ("timeout", r"timeout|timed out|超时"),
        ("network", r"network|connection|connecterror|dns|网络|连接"),
        ("empty_response", r"empty[_ ]?response|空响应|响应为空"),
    )
    for error_type, pattern in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return error_type
    return "unknown"


@dataclass(frozen=True)
class CircuitDecision:
    allowed: bool
    state: str
    reason: str | None
    retry_at: str | None


class CrawlerCircuitBreaker:
    def __init__(
        self,
        *,
        enabled: bool = True,
        failure_threshold: int = 3,
        open_minutes: int = 20,
        failure_window_minutes: int = 5,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if failure_threshold < 1 or open_minutes < 1 or failure_window_minutes < 1:
            raise ValueError("crawler circuit breaker 配置必须为正整数")
        self.enabled = enabled
        self.failure_threshold = failure_threshold
        self.open_minutes = open_minutes
        self.failure_window_minutes = failure_window_minutes
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._state = "closed"
        self._reason: str | None = None
        self._opened_until: datetime | None = None
        self._probe_in_flight = False
        self._failures: dict[str, dict[str, datetime]] = {}

    def before_request(self, *, force: bool = False) -> CircuitDecision:
        with self._lock:
            now = _as_utc(self._now())
            if not self.enabled or force:
                return CircuitDecision(True, self._state, self._reason, self._retry_at())
            if self._state == "open" and self._opened_until is not None and now >= self._opened_until:
                self._state = "half_open"
                self._probe_in_flight = False
            if self._state == "closed":
                return CircuitDecision(True, "closed", None, None)
            if self._state == "half_open" and not self._probe_in_flight:
                self._probe_in_flight = True
                return CircuitDecision(True, "half_open", self._reason, self._retry_at())
            return CircuitDecision(False, self._state, self._reason, self._retry_at())

    def record_success(self, account_id: str) -> None:
        with self._lock:
            for failures in self._failures.values():
                failures.pop(account_id, None)
            if self._state == "half_open":
                self._close()

    def record_failure(self, account_id: str, error_type: str) -> None:
        with self._lock:
            now = _as_utc(self._now())
            if self._state == "half_open":
                self._open(error_type, now)
                return
            if error_type not in GLOBAL_ERROR_TYPES:
                return
            cutoff = now - timedelta(minutes=self.failure_window_minutes)
            failures = self._failures.setdefault(error_type, {})
            for stale_id, failed_at in list(failures.items()):
                if failed_at < cutoff:
                    failures.pop(stale_id, None)
            failures[account_id] = now
            if len(failures) >= self.failure_threshold:
                self._open(error_type, now)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "state": self._state,
                "reason": self._reason,
                "retry_at": self._retry_at(),
            }

    def _open(self, reason: str, now: datetime) -> None:
        self._state = "open"
        self._reason = reason
        self._opened_until = now + timedelta(minutes=self.open_minutes)
        self._probe_in_flight = False

    def _close(self) -> None:
        self._state = "closed"
        self._reason = None
        self._opened_until = None
        self._probe_in_flight = False
        self._failures.clear()

    def _retry_at(self) -> str | None:
        return self._opened_until.isoformat(timespec="seconds") if self._opened_until else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
