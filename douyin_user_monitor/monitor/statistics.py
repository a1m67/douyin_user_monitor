from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from douyin_user_monitor.monitor.profile_parser import ACCOUNT_STATUS_NORMAL

CHINA_TZ = timezone(timedelta(hours=8))
SILENT_7D_DAYS = 7
SILENT_30D_DAYS = 30
TOP_DOWNLOADED_USERS_LIMIT = 12


def build_user_statistics(
    users: List[Dict[str, Any]],
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    current_time = now.astimezone(CHINA_TZ) if now else datetime.now(CHINA_TZ)
    user_rows = [_build_user_row(user, current_time) for user in users if isinstance(user, dict)]
    return {
        "generated_at": current_time.isoformat(timespec="seconds"),
        "summary": _build_summary(user_rows),
        "rankings": {
            "top_downloaded_users": _top_downloaded_users(user_rows),
        },
        "lists": {
            "silent_users_7d": _build_silent_users(user_rows, SILENT_7D_DAYS),
            "silent_users_30d": _build_silent_users(user_rows, SILENT_30D_DAYS),
            "unknown_publish_users": _build_unknown_publish_users(user_rows),
            "deactivated_users": _build_deactivated_users(user_rows),
        },
        "windows": {
            "silent_7d_days": SILENT_7D_DAYS,
            "silent_30d_days": SILENT_30D_DAYS,
        },
        "notes": {
            "downloaded_works": "累计下载作品数基于 downloaded_count 与已保存 aweme_id 去重后的较大值。",
            "content_mix": "视频/图集结构基于本地已保存的 download_records。",
            "silent_rule": "沉默用户仅统计存在最近发布时间记录且早于阈值的用户；无记录用户单独列出。",
            "account_status_rule": "注销/封禁列表仅基于明确状态信号；无明确信号保持正常。",
        },
    }


def _build_summary(user_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_users = len(user_rows)
    total_downloaded_works = sum(user["total_downloaded_works"] for user in user_rows)
    structured_work_count = sum(user["structured_work_count"] for user in user_rows)
    coverage_ratio = _ratio(structured_work_count, total_downloaded_works)
    summary = _count_user_flags(user_rows)
    summary.update(
        {
            "total_users": total_users,
            "paused_users": total_users - summary["enabled_users"],
            "total_downloaded_works": total_downloaded_works,
            "structured_work_count": structured_work_count,
            "known_video_posts": sum(user["known_video_posts"] for user in user_rows),
            "known_image_posts": sum(user["known_image_posts"] for user in user_rows),
            "known_image_assets": sum(user["known_image_assets"] for user in user_rows),
            "structured_total_size_bytes": sum(user["structured_total_size_bytes"] for user in user_rows),
            "structured_coverage_ratio": coverage_ratio,
            "structured_coverage_percent": round(coverage_ratio * 100, 1),
            "avg_downloaded_works_per_user": (
                round(total_downloaded_works / total_users, 1) if total_users else 0.0
            ),
        }
    )
    return summary


def _count_user_flags(user_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "enabled_users": sum(1 for user in user_rows if user["enabled"]),
        "active_users_7d": sum(1 for user in user_rows if user["has_publish_in_7d"]),
        "active_users_30d": sum(1 for user in user_rows if user["has_publish_in_30d"]),
        "silent_users_7d": sum(1 for user in user_rows if user["is_silent_7d"]),
        "silent_users_30d": sum(1 for user in user_rows if user["is_silent_30d"]),
        "unknown_publish_users": sum(1 for user in user_rows if not user["activity_known"]),
        "abnormal_users": sum(1 for user in user_rows if user["is_abnormal_account"]),
        "deleted_users": sum(1 for user in user_rows if user["account_status"] == "deleted"),
        "banned_users": sum(1 for user in user_rows if user["account_status"] == "banned"),
    }


def _build_user_row(user: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    records = _normalize_records(user.get("download_records"))
    activity = _build_user_activity(user, records, now)
    content = _build_user_content_metrics(user, records)
    identity = _build_user_identity(user)
    identity.update(content)
    identity.update(activity)
    return identity



def _as_stripped_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    text = _as_stripped_str(value)
    return text or None


def _build_user_identity(user: Dict[str, Any]) -> Dict[str, Any]:
    account_status = _as_stripped_str(user.get("account_status")) or ACCOUNT_STATUS_NORMAL
    nickname = _as_stripped_str(user.get("nickname")) or _as_stripped_str(user.get("sec_user_id")) or "-"
    return {
        "id": _as_stripped_str(user.get("id")),
        "nickname": nickname,
        "sec_user_id": _as_stripped_str(user.get("sec_user_id")),
        "profile_url": _optional_text(user.get("profile_url")),
        "avatar_url": _optional_text(user.get("avatar_url")),
        "enabled": bool(user.get("enabled", True)),
        "account_status": account_status,
        "account_status_label": _as_stripped_str(user.get("account_status_label")) or "正常",
        "account_status_reason": _optional_text(user.get("account_status_reason")),
        "account_status_updated_at": _optional_text(user.get("account_status_updated_at")),
        "last_checked_at": _optional_text(user.get("last_checked_at")),
        "last_error": _optional_text(user.get("last_error")),
        "is_abnormal_account": account_status != ACCOUNT_STATUS_NORMAL,
    }


def _build_user_content_metrics(user: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_downloaded_works = max(
        _safe_int(user.get("downloaded_count")),
        _unique_non_empty_count(user.get("downloaded_aweme_ids")),
        len(records),
    )
    return {
        "total_downloaded_works": total_downloaded_works,
        "structured_work_count": len(records),
        "known_video_posts": sum(1 for record in records if record.get("media_type") == "video"),
        "known_image_posts": sum(1 for record in records if record.get("media_type") == "image"),
        "known_image_assets": sum(_safe_int(record.get("image_count")) for record in records),
        "structured_total_size_bytes": sum(_safe_int(record.get("total_size_bytes")) for record in records),
    }


def _build_user_activity(
    user: Dict[str, Any],
    records: List[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    publish_times = _extract_times(records, "publish_time")
    download_times = _extract_times(records, "downloaded_at")
    last_publish_at = max(publish_times) if publish_times else None
    last_download_at = _parse_datetime(user.get("last_download_at")) or _last_or_none(download_times)
    return {
        "last_publish_at": _format_datetime(last_publish_at),
        "last_download_at": _format_datetime(last_download_at),
        "days_since_last_publish": _days_since(now, last_publish_at),
        "days_since_last_download": _days_since(now, last_download_at),
        "activity_known": last_publish_at is not None,
        "has_publish_in_7d": _is_recent(last_publish_at, now, SILENT_7D_DAYS),
        "has_publish_in_30d": _is_recent(last_publish_at, now, SILENT_30D_DAYS),
        "is_silent_7d": _is_silent(last_publish_at, now, SILENT_7D_DAYS),
        "is_silent_30d": _is_silent(last_publish_at, now, SILENT_30D_DAYS),
    }


def _build_silent_users(user_rows: List[Dict[str, Any]], days: int) -> List[Dict[str, Any]]:
    key = "is_silent_7d" if days == SILENT_7D_DAYS else "is_silent_30d"
    silent_users = [user for user in user_rows if user[key]]
    silent_users.sort(key=lambda user: _timestamp_or_zero(user.get("last_publish_at")))
    return silent_users


def _build_unknown_publish_users(user_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unknown_users = [user for user in user_rows if not user["activity_known"]]
    unknown_users.sort(key=lambda user: str(user.get("nickname") or ""))
    return unknown_users


def _build_deactivated_users(user_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deactivated = [user for user in user_rows if user["is_abnormal_account"]]
    deactivated.sort(
        key=lambda user: (
            0 if user["account_status"] == "deleted" else 1,
            str(user.get("nickname") or ""),
        )
    )
    return deactivated


def _top_downloaded_users(user_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(
        user_rows,
        key=lambda user: (
            -user["total_downloaded_works"],
            -_timestamp_or_zero(user.get("last_download_at")),
            str(user.get("nickname") or ""),
        ),
    )
    return ranked[:TOP_DOWNLOADED_USERS_LIMIT]


def _normalize_records(raw_records: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_records, list):
        return []
    return [record for record in raw_records if isinstance(record, dict)]


def _extract_times(records: List[Dict[str, Any]], key: str) -> List[datetime]:
    values: List[datetime] = []
    for record in records:
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_datetime(raw_value: Any) -> datetime | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _days_since(now: datetime, value: datetime | None) -> int | None:
    if value is None:
        return None
    delta = now - value
    return max(0, int(delta.total_seconds() // 86400))


def _is_recent(value: datetime | None, now: datetime, days: int) -> bool:
    if value is None:
        return False
    return value >= now - timedelta(days=days)


def _is_silent(value: datetime | None, now: datetime, days: int) -> bool:
    if value is None:
        return False
    return value < now - timedelta(days=days)


def _last_or_none(values: List[datetime]) -> datetime | None:
    if not values:
        return None
    return max(values)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _unique_non_empty_count(values: Any) -> int:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, dict)):
        return 0
    return len({str(value).strip() for value in values if str(value).strip()})


def _timestamp_or_zero(raw_value: Any) -> float:
    parsed = _parse_datetime(raw_value)
    if parsed is None:
        return 0.0
    return parsed.timestamp()
