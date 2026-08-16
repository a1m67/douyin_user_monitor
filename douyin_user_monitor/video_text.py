"""Normalize confirmed Douyin text fields for parsing and display."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_SERIES_PREFIX_FIELD = "series_play_info.item_title_prefix.text"
_ITEM_TITLE_FIELD = "item_title"
_DESCRIPTION_FIELD = "desc"


@dataclass(frozen=True)
class VideoTextMetadata:
    display_title: str | None
    text_sources: Mapping[str, str]


def build_video_text_metadata(
    raw: Mapping[str, Any] | str | None,
    *,
    description: str = "",
    display_title: str | None = None,
    text_sources: Mapping[str, Any] | None = None,
) -> VideoTextMetadata:
    """Build parser-ready sources from fields observed in Douyin list payloads."""
    sources = _confirmed_douyin_sources(_mapping(raw))
    sources.update(_normalized_text_sources(text_sources))

    fallback_description = _text(description)
    if fallback_description and not any(value == fallback_description for value in sources.values()):
        sources["description"] = fallback_description

    resolved_display_title = _text(display_title) or _display_title(sources)
    return VideoTextMetadata(
        display_title=resolved_display_title,
        text_sources=sources,
    )


def _confirmed_douyin_sources(raw: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    prefix = _nested_text(raw, "series_play_info", "item_title_prefix", "text")
    item_title = _text(raw.get("item_title"))
    description = _text(raw.get("desc"))
    if prefix:
        result[_SERIES_PREFIX_FIELD] = prefix
    if item_title:
        result[_ITEM_TITLE_FIELD] = item_title
    if description:
        result[_DESCRIPTION_FIELD] = description
    return result


def _display_title(sources: Mapping[str, str]) -> str | None:
    prefix = _text(sources.get(_SERIES_PREFIX_FIELD))
    item_title = _text(sources.get(_ITEM_TITLE_FIELD))
    if prefix and item_title:
        return f"{prefix} | {item_title}"
    return item_title or prefix or None


def _mapping(raw: Mapping[str, Any] | str | None) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


def _nested_text(raw: Mapping[str, Any], *keys: str) -> str | None:
    value: Any = raw
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return _text(value)


def _normalized_text_sources(value: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_field, raw_text in value.items():
        field = str(raw_field or "").strip()
        text = _text(raw_text)
        if field and text:
            result[field] = text
    return result


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
