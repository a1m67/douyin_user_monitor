"""Stable API response serializers for service result objects."""
from __future__ import annotations

from typing import Any

from douyin_user_monitor.services.episode_pipeline import HistoryBackfillResult, SyncResult


def sync_result(result: SyncResult) -> dict[str, Any]:
    return {
        "account_id": result.account["id"], "initial_sync": result.initial_sync,
        "fetched_videos": result.fetched_videos, "new_videos": result.new_videos,
        "duplicate_videos": result.duplicate_videos, "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos,
        "new_episode_count": len(result.new_episode_updates),
    }


def history_backfill_result(result: HistoryBackfillResult) -> dict[str, Any]:
    return {
        "account_id": result.account["id"], "history_sync": result.account["history_sync"],
        "fetched_videos": result.fetched_videos, "new_videos": result.new_videos,
        "duplicate_videos": result.duplicate_videos, "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos,
    }


def reparse_result(result: Any) -> dict[str, Any]:
    return {
        "account_id": result.account["id"], "requested_videos": result.requested_videos,
        "matched_videos": result.matched_videos, "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos, "new_episode_count": result.new_episode_count,
    }
