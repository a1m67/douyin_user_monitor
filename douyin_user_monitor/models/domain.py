"""Explicit data contracts for short-drama tracking entities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Account:
    id: str
    nickname: str
    sec_uid: str
    homepage_url: str
    enabled: bool
    check_interval_minutes: int
    last_checked_at: str | None
    next_check_at: str | None
    last_success_at: str | None
    last_error: str | None
    consecutive_failures: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Video:
    id: int
    aweme_id: str
    account_id: str
    description: str
    hashtags: tuple[str, ...]
    publish_time: str | None
    video_url: str
    cover_url: str | None
    raw_json: str
    display_title: str | None
    text_sources: Mapping[str, str]
    is_processed: bool
    needs_review: bool
    classification_status: str
    parser_confidence: float | None
    parser_reason: str | None
    show_title_candidate: str | None
    episode_candidate: int | None
    content_type: str
    parser_evidence: Mapping[str, Any]
    created_at: str
    processed_at: str | None


@dataclass(frozen=True)
class Show:
    id: int
    title: str
    normalized_title: str
    aliases: tuple[str, ...]
    latest_season: int | None
    latest_episode: int | None
    latest_update_at: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Episode:
    id: int
    show_id: int
    season_number: int
    episode_number: int
    first_video_id: int
    first_account_id: str
    published_at: str | None
    created_at: str


@dataclass(frozen=True)
class EpisodeSource:
    id: int
    episode_id: int
    video_id: int
    account_id: str
    published_at: str | None
    created_at: str


@dataclass(frozen=True)
class Notification:
    id: int
    show_id: int
    episode_id: int
    channel: str
    success: bool
    error: str | None
    sent_at: str
