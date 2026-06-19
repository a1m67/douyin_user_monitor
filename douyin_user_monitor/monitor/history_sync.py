from __future__ import annotations

from typing import Any, Dict, List

HISTORY_SYNC_STATUS_PENDING = "pending"
HISTORY_SYNC_STATUS_RUNNING = "running"
HISTORY_SYNC_STATUS_PAUSED = "paused"
HISTORY_SYNC_STATUS_COMPLETED = "completed"
HISTORY_SYNC_STATUS_FAILED = "failed"
HISTORY_SYNC_STATUS_IDLE = "idle"

HISTORY_SYNC_PAGE_SIZE = 50
ACTIVE_HISTORY_SYNC_STATUSES = {
    HISTORY_SYNC_STATUS_PENDING,
    HISTORY_SYNC_STATUS_RUNNING,
    HISTORY_SYNC_STATUS_FAILED,
    HISTORY_SYNC_STATUS_IDLE,
}
KNOWN_HISTORY_SYNC_STATUSES = {
    HISTORY_SYNC_STATUS_PENDING,
    HISTORY_SYNC_STATUS_RUNNING,
    HISTORY_SYNC_STATUS_PAUSED,
    HISTORY_SYNC_STATUS_COMPLETED,
    HISTORY_SYNC_STATUS_FAILED,
    HISTORY_SYNC_STATUS_IDLE,
}


def build_history_sync_state(
    *,
    status: str,
    next_cursor: int = 0,
    page_size: int = HISTORY_SYNC_PAGE_SIZE,
    processed_pages: int = 0,
    scanned_items: int = 0,
    downloaded_items: int = 0,
    has_more: bool = True,
    started_at: str | None = None,
    updated_at: str | None = None,
    completed_at: str | None = None,
    last_error: str | None = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "next_cursor": next_cursor,
        "page_size": page_size,
        "processed_pages": processed_pages,
        "scanned_items": scanned_items,
        "downloaded_items": downloaded_items,
        "has_more": has_more,
        "started_at": started_at,
        "updated_at": updated_at,
        "completed_at": completed_at,
        "last_error": last_error,
    }


def normalize_history_sync_state(user: Dict[str, Any]) -> Dict[str, Any]:
    fallback = build_history_sync_state(status=_default_status(user))
    raw = user.get("history_sync")
    if not isinstance(raw, dict):
        user["history_sync"] = fallback
        return fallback

    normalized = build_history_sync_state(
        status=_normalize_status(raw.get("status"), fallback["status"]),
        next_cursor=max(0, _safe_int(raw.get("next_cursor"))),
        page_size=max(1, _safe_int(raw.get("page_size")) or HISTORY_SYNC_PAGE_SIZE),
        processed_pages=max(0, _safe_int(raw.get("processed_pages"))),
        scanned_items=max(0, _safe_int(raw.get("scanned_items"))),
        downloaded_items=max(0, _safe_int(raw.get("downloaded_items"))),
        has_more=bool(raw.get("has_more", fallback["has_more"])),
        started_at=_clean_text(raw.get("started_at")),
        updated_at=_clean_text(raw.get("updated_at")),
        completed_at=_clean_text(raw.get("completed_at")),
        last_error=_clean_text(raw.get("last_error")),
    )
    if normalized["status"] == HISTORY_SYNC_STATUS_COMPLETED:
        normalized["has_more"] = False
    user["history_sync"] = normalized
    return normalized


def update_history_sync_progress(
    *,
    history_sync: Dict[str, Any],
    page: Dict[str, Any],
    scanned_count: int,
    downloaded_count: int,
    errors: List[str],
    now: str,
) -> None:
    next_cursor = _extract_next_cursor(page, int(history_sync["next_cursor"]))
    has_more = _extract_has_more(page, next_cursor)
    history_sync["status"] = HISTORY_SYNC_STATUS_RUNNING if has_more else HISTORY_SYNC_STATUS_COMPLETED
    history_sync["next_cursor"] = next_cursor
    history_sync["processed_pages"] = int(history_sync["processed_pages"]) + 1
    history_sync["scanned_items"] = int(history_sync["scanned_items"]) + scanned_count
    history_sync["downloaded_items"] = int(history_sync["downloaded_items"]) + downloaded_count
    history_sync["has_more"] = has_more
    history_sync["started_at"] = history_sync.get("started_at") or now
    history_sync["updated_at"] = now
    history_sync["completed_at"] = now if not has_more else None
    history_sync["last_error"] = "\n".join(errors)[:1000] if errors else None
    if errors and has_more:
        history_sync["status"] = HISTORY_SYNC_STATUS_FAILED


def complete_history_sync(history_sync: Dict[str, Any], *, now: str) -> None:
    history_sync["status"] = HISTORY_SYNC_STATUS_COMPLETED
    history_sync["has_more"] = False
    history_sync["updated_at"] = now
    history_sync["completed_at"] = now
    history_sync["last_error"] = None


def _default_status(user: Dict[str, Any]) -> str:
    has_downloads = bool(user.get("downloaded_count")) or bool(user.get("downloaded_aweme_ids"))
    has_records = bool(user.get("download_records"))
    has_checked = bool(str(user.get("last_checked_at") or "").strip())
    if has_downloads or has_records or has_checked:
        return HISTORY_SYNC_STATUS_IDLE
    return HISTORY_SYNC_STATUS_PENDING


def _normalize_status(value: Any, default_value: str) -> str:
    status = str(value or "").strip().lower()
    if status in KNOWN_HISTORY_SYNC_STATUSES:
        return status
    return default_value


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_next_cursor(page: Dict[str, Any], fallback: int) -> int:
    for key in ("next_cursor", "max_cursor", "cursor"):
        value = _safe_int(page.get(key))
        if value > fallback:
            return value
    return fallback


def _extract_has_more(page: Dict[str, Any], next_cursor: int) -> bool:
    raw = page.get("has_more")
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        return _safe_int(raw) > 0
    return next_cursor > 0 and next_cursor != _safe_int(page.get("cursor"))
