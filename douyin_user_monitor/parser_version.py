"""Stable parser processing identity without persisting provider secrets."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence


PARSER_VERSION = "2026.08.1"


def parser_input_hash(
    *,
    display_title: str | None,
    description: str | None,
    hashtags: Sequence[Any] | None,
    text_sources: Mapping[str, Any] | None,
) -> str:
    payload = {
        "display_title": str(display_title or ""),
        "description": str(description or ""),
        "hashtags": [str(value) for value in (hashtags or ())],
        "text_sources": dict(text_sources or {}),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def video_parser_input_hash(video: Mapping[str, Any]) -> str:
    return parser_input_hash(
        display_title=video.get("display_title"),
        description=video.get("description"),
        hashtags=video.get("hashtags") or (),
        text_sources=video.get("text_sources") or {},
    )


@lru_cache(maxsize=1)
def current_build_sha() -> str:
    for key in ("BUILD_SHA", "GITHUB_SHA", "SOURCE_VERSION"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value[:64]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = result.stdout.strip()
        if value:
            return value[:64]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"
