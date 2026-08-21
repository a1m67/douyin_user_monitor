"""Bounded storage projection for crawler payloads kept for audit/reparse."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


RAW_STORAGE_VERSION = 1


def compact_video_raw(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"_storage_version": RAW_STORAGE_VERSION}
    result: dict[str, Any] = {"_storage_version": RAW_STORAGE_VERSION}
    for key in ("aweme_id", "desc", "item_title", "create_time", "share_url", "aweme_url"):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            result[key] = value
    prefix = _nested(raw, "series_play_info", "item_title_prefix", "text")
    if prefix not in (None, ""):
        result["series_play_info"] = {"item_title_prefix": {"text": prefix}}
    tags: list[dict[str, str]] = []
    seen_tags: set[str] = set()
    text_extra = raw.get("text_extra")
    if isinstance(text_extra, list):
        for item in text_extra:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("hashtag_name") or "").strip()
            if name and name not in seen_tags:
                tags.append({"hashtag_name": name})
                seen_tags.add(name)
            if len(tags) >= 50:
                break
    if tags:
        result["text_extra"] = tags
    return result


def _nested(raw: Mapping[str, Any], *keys: str) -> Any:
    value: Any = raw
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value
