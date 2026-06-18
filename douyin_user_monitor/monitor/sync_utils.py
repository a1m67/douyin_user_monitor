from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Set

CHINA_TZ = timezone(timedelta(hours=8))
MAX_DOWNLOAD_RECORDS_PER_USER = 300


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def format_publish_time(create_time: Any) -> str | None:
    timestamp = safe_int(create_time)
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=CHINA_TZ).isoformat(timespec="seconds")


def merge_downloaded_ids(existing_ids: List[str], new_ids: List[str]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for aweme_id in [*existing_ids, *new_ids]:
        aweme_id_str = str(aweme_id).strip()
        if not aweme_id_str or aweme_id_str in seen:
            continue
        seen.add(aweme_id_str)
        merged.append(aweme_id_str)
    return merged


def merge_download_records(
    existing_records: List[Dict[str, Any]],
    new_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen_aweme_ids: Set[str] = set()
    for record in [*reversed(new_records), *existing_records]:
        if not isinstance(record, dict):
            continue
        aweme_id = str(record.get("aweme_id", "")).strip()
        if not aweme_id or aweme_id in seen_aweme_ids:
            continue
        seen_aweme_ids.add(aweme_id)
        merged.append(record)
        if len(merged) >= MAX_DOWNLOAD_RECORDS_PER_USER:
            break
    return merged
