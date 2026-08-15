"""Unified notification contract for newly discovered episodes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from douyin_user_monitor.services.episode_pipeline import EpisodeUpdate


@dataclass(frozen=True)
class EpisodeNotification:
    show_id: int
    episode_id: int
    show_title: str
    episode_number: int
    account_nickname: str
    published_at: str | None
    video_url: str
    cover_url: str | None

    @classmethod
    def from_update(cls, update: EpisodeUpdate) -> "EpisodeNotification":
        return cls(
            show_id=int(update.show["id"]),
            episode_id=int(update.episode["id"]),
            show_title=str(update.show["title"]),
            episode_number=int(update.episode["episode_number"]),
            account_nickname=str(update.account["nickname"]),
            published_at=update.video.get("publish_time"),
            video_url=str(update.video.get("video_url") or ""),
            cover_url=update.video.get("cover_url"),
        )


class Notifier(Protocol):
    """Notification implementation used by the post-transaction dispatcher."""

    channel: str

    async def send_episode_update(self, notification: EpisodeNotification) -> None:
        ...

    async def aclose(self) -> None:
        ...
