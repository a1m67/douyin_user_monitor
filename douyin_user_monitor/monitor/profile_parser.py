from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from douyin_user_monitor.monitor.downloader import sanitize_text

ACCOUNT_STATUS_NORMAL = "normal"
ACCOUNT_STATUS_DELETED = "deleted"
ACCOUNT_STATUS_BANNED = "banned"
ACCOUNT_STATUS_LABELS = {
    ACCOUNT_STATUS_NORMAL: "正常",
    ACCOUNT_STATUS_DELETED: "已注销",
    ACCOUNT_STATUS_BANNED: "已封禁",
}
AVATAR_CANDIDATE_PATHS = (
    ("user", "avatar_thumb", "url_list"),
    ("user", "avatar_168x168", "url_list"),
    ("user", "avatar_medium", "url_list"),
    ("user", "avatar_300x300", "url_list"),
    ("user", "avatar_larger", "url_list"),
    ("user_info", "avatar_thumb", "url_list"),
)


@dataclass(frozen=True)
class UserProfileSnapshot:
    nickname: str
    avatar_url: str | None
    account_status: str
    account_status_label: str
    account_status_reason: str | None


def extract_nickname(profile_data: Dict[str, Any]) -> str:
    candidates = [
        profile_data.get("user", {}).get("nickname", ""),
        profile_data.get("user_info", {}).get("nickname", ""),
        profile_data.get("nickname", ""),
    ]
    for value in candidates:
        safe_value = sanitize_text(value, "")
        if safe_value:
            return safe_value
    return ""


def extract_avatar_url(profile_data: Dict[str, Any]) -> str | None:
    for path in AVATAR_CANDIDATE_PATHS:
        value = _read_nested(profile_data, path)
        avatar_url = _first_non_empty_url(value)
        if avatar_url:
            return avatar_url
    return None


def extract_account_status(profile_data: Dict[str, Any]) -> Dict[str, str | None]:
    status_texts = _collect_status_texts(profile_data)
    deleted_reason = _detect_deleted_reason(profile_data, status_texts)
    if deleted_reason is not None:
        return build_account_status_fields(ACCOUNT_STATUS_DELETED, deleted_reason)

    banned_reason = _detect_banned_reason(profile_data)
    if banned_reason is not None:
        return build_account_status_fields(ACCOUNT_STATUS_BANNED, banned_reason)
    return build_account_status_fields(ACCOUNT_STATUS_NORMAL, None)


def build_profile_snapshot_fields(profile: UserProfileSnapshot, *, updated_at: str) -> Dict[str, Any]:
    return {
        "nickname": profile.nickname,
        "avatar_url": profile.avatar_url,
        "account_status": profile.account_status,
        "account_status_label": profile.account_status_label,
        "account_status_reason": profile.account_status_reason,
        "account_status_updated_at": updated_at,
    }


def build_account_status_fields(status: str, reason: str | None) -> Dict[str, str | None]:
    safe_status = _normalize_account_status(status)
    safe_reason = sanitize_text(reason, "") or None
    if safe_status == ACCOUNT_STATUS_NORMAL:
        safe_reason = None
    return {
        "account_status": safe_status,
        "account_status_label": ACCOUNT_STATUS_LABELS[safe_status],
        "account_status_reason": safe_reason,
    }


def _collect_status_texts(profile_data: Dict[str, Any]) -> list[str]:
    paths = (
        ("user", "special_state_info", "title"),
        ("user", "special_state_info", "content"),
        ("special_state_info", "title"),
        ("special_state_info", "content"),
        ("user", "status_msg"),
        ("status_msg",),
    )
    texts: list[str] = []
    for path in paths:
        value = _read_nested(profile_data, path)
        text = str(value or "").strip()
        if text:
            texts.append(text)
    return texts


def _detect_deleted_reason(profile_data: Dict[str, Any], status_texts: list[str]) -> str | None:
    user_deleted = _read_nested(profile_data, ("user", "user_deleted"))
    preferred_reason = _first_status_text(status_texts)
    if user_deleted is True:
        return preferred_reason or "user.user_deleted=true"
    for text in status_texts:
        if "注销" in text:
            return text
    return None


def _detect_banned_reason(profile_data: Dict[str, Any]) -> str | None:
    titles = (
        _read_nested(profile_data, ("user", "special_state_info", "title")),
        _read_nested(profile_data, ("special_state_info", "title")),
    )
    for title in titles:
        text = sanitize_text(title, "")
        if text in {"账号已封禁", "该账号已封禁", "账号封禁", "已封禁"}:
            return text
    return None


def _read_nested(data: Dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _first_non_empty_url(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        text = str(item or "").strip()
        if text:
            return text
    return None


def _normalize_account_status(status: str) -> str:
    safe_status = sanitize_text(status, ACCOUNT_STATUS_NORMAL)
    if safe_status in ACCOUNT_STATUS_LABELS:
        return safe_status
    return ACCOUNT_STATUS_NORMAL


def _first_status_text(status_texts: list[str]) -> str | None:
    for text in status_texts:
        if text:
            return text
    return None
