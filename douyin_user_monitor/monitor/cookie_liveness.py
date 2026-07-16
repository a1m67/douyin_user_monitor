from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence

from douyin_user_monitor.monitor.profile_parser import ACCOUNT_STATUS_NORMAL
from douyin_user_monitor.monitor.storage import utc_now

STATUS_HEALTHY = "healthy"
STATUS_EXPIRED = "expired"
STATUS_UNKNOWN = "unknown"
SECONDS_PER_DAY = 86400
SAMPLE_POST_COUNT = 5


class CookieProbeCrawler(Protocol):
    async def fetch_user_post_videos(
        self,
        sec_user_id: str,
        max_cursor: int,
        count: int,
    ) -> Dict[str, Any]:
        ...


class CookieAlertSender(Protocol):
    async def send(self, message: str) -> None:
        ...


@dataclass(frozen=True)
class CookieLivenessConfig:
    enabled: bool
    interval_hours: float
    stale_days: float
    sample_user_count: int
    min_samples: int
    alert_cooldown_hours: float


@dataclass(frozen=True)
class CookieLivenessResult:
    status: str
    reason: str
    samples_used: int
    newest_create_time: Optional[int]
    samples: List[Dict[str, Any]]


def evaluate_cookie_liveness(
    samples: Sequence[Dict[str, Any]],
    *,
    now_ts: int,
    stale_seconds: int,
    min_samples: int,
) -> CookieLivenessResult:
    ok_samples = [item for item in samples if item.get("error") is None]
    if len(ok_samples) < min_samples:
        return CookieLivenessResult(
            status=STATUS_UNKNOWN,
            reason=f"成功样本不足: {len(ok_samples)}/{min_samples}",
            samples_used=len(ok_samples),
            newest_create_time=_max_create_time(ok_samples),
            samples=list(samples),
        )

    newest = _max_create_time(ok_samples)
    if newest is None:
        return CookieLivenessResult(
            status=STATUS_EXPIRED,
            reason="成功样本均无作品 create_time",
            samples_used=len(ok_samples),
            newest_create_time=None,
            samples=list(samples),
        )

    age_seconds = now_ts - int(newest)
    if age_seconds <= stale_seconds:
        return CookieLivenessResult(
            status=STATUS_HEALTHY,
            reason=f"存在 {stale_seconds // SECONDS_PER_DAY} 天内作品",
            samples_used=len(ok_samples),
            newest_create_time=int(newest),
            samples=list(samples),
        )

    return CookieLivenessResult(
        status=STATUS_EXPIRED,
        reason=f"全部样本最新作品早于 {stale_seconds // SECONDS_PER_DAY} 天",
        samples_used=len(ok_samples),
        newest_create_time=int(newest),
        samples=list(samples),
    )


def should_send_cookie_alert(
    *,
    status: str,
    previous_status: Optional[str],
    last_alert_at: Optional[str],
    now: datetime,
    cooldown_hours: float,
) -> bool:
    if status != STATUS_EXPIRED:
        return False
    if previous_status != STATUS_EXPIRED:
        return True
    if not last_alert_at:
        return True
    last_alert = _parse_iso_datetime(last_alert_at)
    if last_alert is None:
        return True
    return now - last_alert >= timedelta(hours=cooldown_hours)


def select_probe_users(users: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for user in users:
        if not user.get("enabled", True):
            continue
        status = str(user.get("account_status") or ACCOUNT_STATUS_NORMAL).strip()
        if status != ACCOUNT_STATUS_NORMAL:
            continue
        sec_user_id = str(user.get("sec_user_id") or "").strip()
        if not sec_user_id:
            continue
        candidates.append(user)
    candidates.sort(key=lambda item: str(item.get("id") or ""))
    return candidates[: max(0, limit)]


def extract_latest_create_time(aweme_list: Any) -> Optional[int]:
    if not isinstance(aweme_list, list) or not aweme_list:
        return None
    values: List[int] = []
    for item in aweme_list:
        if not isinstance(item, dict):
            continue
        raw = item.get("create_time")
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            values.append(int(raw))
            continue
        if isinstance(raw, str) and raw.strip().isdigit():
            values.append(int(raw.strip()))
    if not values:
        return None
    return max(values)


def is_check_due(
    *,
    last_check_at: Optional[str],
    now: datetime,
    interval_hours: float,
) -> bool:
    if not last_check_at:
        return True
    last_check = _parse_iso_datetime(last_check_at)
    if last_check is None:
        return True
    return now - last_check >= timedelta(hours=interval_hours)


class CookieLivenessService:
    def __init__(
        self,
        *,
        crawler: CookieProbeCrawler,
        config: CookieLivenessConfig,
        alerter: Optional[CookieAlertSender] = None,
        now_provider: Optional[Any] = None,
    ):
        self._crawler = crawler
        self._config = config
        self._alerter = alerter
        self._now_provider = now_provider or _default_now

    async def maybe_run(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self._config.enabled:
            return None

        monitoring = state.setdefault("monitoring", {})
        record = monitoring.setdefault("cookie_liveness", {})
        now = self._now_provider()
        if not is_check_due(
            last_check_at=record.get("last_check_at"),
            now=now,
            interval_hours=self._config.interval_hours,
        ):
            return None

        result = await self.run_check(state["users"])
        previous_status = str(record.get("status") or "") or None
        last_alert_at = record.get("last_alert_at")
        alerted = False
        alert_error = None

        if should_send_cookie_alert(
            status=result.status,
            previous_status=previous_status,
            last_alert_at=str(last_alert_at) if last_alert_at else None,
            now=now,
            cooldown_hours=self._config.alert_cooldown_hours,
        ):
            if self._alerter is None:
                alert_error = "cookie 失效但未配置 hermes 微信告警"
            else:
                try:
                    await self._alerter.send(self._build_alert_message(result))
                    alerted = True
                    last_alert_at = utc_now()
                except Exception as exc:  # noqa: BLE001
                    alert_error = str(exc)

        payload = {
            "last_check_at": utc_now(),
            "status": result.status,
            "reason": result.reason,
            "newest_create_time": result.newest_create_time,
            "samples": result.samples,
            "samples_used": result.samples_used,
            "last_alert_at": last_alert_at,
            "last_error": alert_error,
            "alerted": alerted,
            "updated_at": utc_now(),
        }
        monitoring["cookie_liveness"] = payload
        monitoring["updated_at"] = utc_now()
        return payload

    async def run_check(self, users: Sequence[Dict[str, Any]]) -> CookieLivenessResult:
        probe_users = select_probe_users(users, self._config.sample_user_count)
        samples: List[Dict[str, Any]] = []
        for user in probe_users:
            samples.append(await self._probe_user(user))

        now_ts = int(self._now_provider().timestamp())
        stale_seconds = int(self._config.stale_days * SECONDS_PER_DAY)
        return evaluate_cookie_liveness(
            samples,
            now_ts=now_ts,
            stale_seconds=stale_seconds,
            min_samples=self._config.min_samples,
        )

    async def _probe_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        user_id = str(user.get("id") or "")
        nickname = str(user.get("nickname") or "")
        sec_user_id = str(user.get("sec_user_id") or "")
        try:
            data = await self._crawler.fetch_user_post_videos(
                sec_user_id,
                max_cursor=0,
                count=SAMPLE_POST_COUNT,
            )
            aweme_list = data.get("aweme_list") if isinstance(data, dict) else None
            latest = extract_latest_create_time(aweme_list)
            return {
                "user_id": user_id,
                "nickname": nickname,
                "sec_user_id": sec_user_id,
                "latest_create_time": latest,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "user_id": user_id,
                "nickname": nickname,
                "sec_user_id": sec_user_id,
                "latest_create_time": None,
                "error": str(exc),
            }

    def _build_alert_message(self, result: CookieLivenessResult) -> str:
        newest_text = "-"
        if result.newest_create_time is not None:
            newest_text = datetime.fromtimestamp(
                result.newest_create_time,
                tz=timezone(timedelta(hours=8)),
            ).isoformat(timespec="seconds")
        sample_lines = []
        for item in result.samples[:5]:
            ts = item.get("latest_create_time")
            ts_text = "-" if ts is None else str(ts)
            err = item.get("error")
            suffix = f" error={err}" if err else f" create_time={ts_text}"
            sample_lines.append(f"- {item.get('nickname') or item.get('user_id')}:{suffix}")
        lines = [
            "【抖音监控】Cookie 疑似失效",
            f"状态: {result.status}",
            f"原因: {result.reason}",
            f"有效样本: {result.samples_used}",
            f"最新作品时间: {newest_text}",
            "样本:",
            *sample_lines,
            "请尽快更新上游 Douyin Cookie。",
        ]
        return "\n".join(lines)


def _max_create_time(samples: Sequence[Dict[str, Any]]) -> Optional[int]:
    values = [
        int(item["latest_create_time"])
        for item in samples
        if isinstance(item.get("latest_create_time"), int)
    ]
    if not values:
        return None
    return max(values)


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed


def _default_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))
