from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class VideoSource:
    url: str
    bit_rate: int = 0
    width: int = 0
    height: int = 0
    gear_name: str = ""

    def to_record(self) -> Dict[str, Any]:
        return {
            "bit_rate": self.bit_rate,
            "width": self.width,
            "height": self.height,
            "gear_name": self.gear_name,
        }


def extract_video_source(aweme_detail: Dict[str, Any]) -> Optional[VideoSource]:
    video_data = aweme_detail.get("video", {})
    if not isinstance(video_data, dict):
        return None
    return select_clearest_video_source(video_data) or select_fallback_video_source(video_data)


def select_clearest_video_source(video_data: Dict[str, Any]) -> Optional[VideoSource]:
    """Pick the clearest playable source from video.bit_rate.

    Priority: short-side resolution > pixels > bit_rate > non-low gear.
    Bitrate-first is avoided because Douyin often marks 540p with a higher
    peak bitrate than 720p/1080p.
    """
    candidates: List[VideoSource] = []
    bit_rates = video_data.get("bit_rate", [])
    if not isinstance(bit_rates, list):
        return None
    for item in bit_rates:
        candidate = build_bit_rate_source(item)
        if candidate:
            candidates.append(candidate)
    return pick_clearest_source(candidates)


def select_highest_bit_rate_source(video_data: Dict[str, Any]) -> Optional[VideoSource]:
    """Backward-compatible alias for older call sites / tests."""
    return select_clearest_video_source(video_data)


def build_bit_rate_source(item: Any) -> Optional[VideoSource]:
    if not isinstance(item, dict):
        return None
    play_addr = item.get("play_addr", {})
    if not isinstance(play_addr, dict):
        return None
    url = first_url(play_addr)
    if not url:
        return None
    return VideoSource(
        url=url,
        bit_rate=safe_int(item.get("bit_rate")),
        width=safe_int(play_addr.get("width") or item.get("width")),
        height=safe_int(play_addr.get("height") or item.get("height")),
        gear_name=str(item.get("gear_name") or ""),
    )


def select_fallback_video_source(video_data: Dict[str, Any]) -> Optional[VideoSource]:
    candidates: List[VideoSource] = []
    for key in ["play_addr", "play_addr_h264", "download_addr"]:
        play_addr_data = video_data.get(key, {})
        if not isinstance(play_addr_data, dict):
            continue
        url = first_url(play_addr_data)
        if not url:
            continue
        candidates.append(
            VideoSource(
                url=url,
                width=safe_int(play_addr_data.get("width")),
                height=safe_int(play_addr_data.get("height")),
                gear_name=key,
            )
        )
    return pick_clearest_source(candidates)


def pick_clearest_source(candidates: List[VideoSource]) -> Optional[VideoSource]:
    if not candidates:
        return None
    return max(candidates, key=clarity_score)


def clarity_score(source: VideoSource) -> tuple:
    short_side = min(source.width, source.height) if source.width and source.height else 0
    pixels = source.width * source.height
    gear = (source.gear_name or "").lower()
    # low_/lower_ are degraded encodes; only used as a final tie-break.
    gear_penalty = 1 if gear.startswith(("low_", "lower_")) else 0
    return (short_side, pixels, source.bit_rate, -gear_penalty)


def first_url(play_addr_data: Dict[str, Any]) -> Optional[str]:
    url_list = play_addr_data.get("url_list", [])
    if not isinstance(url_list, list) or not url_list:
        return None
    first = url_list[0]
    if not isinstance(first, str):
        return None
    return first.strip() or None


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
