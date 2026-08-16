"""Stable, crawler-independent Douyin provider contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ProviderAccount:
    """The account identity a provider needs to retrieve data."""

    id: str
    sec_uid: str
    homepage_url: str = ""


@dataclass(frozen=True)
class ProviderProfile:
    nickname: str
    avatar_url: str | None = None


@dataclass(frozen=True)
class ProviderVideo:
    aweme_id: str
    description: str
    hashtags: tuple[str, ...]
    publish_time: str | None
    video_url: str
    cover_url: str | None
    raw: Mapping[str, Any]
    display_title: str | None = None
    text_sources: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderVideoPage:
    """One cursor-addressable page of a creator's published videos."""

    videos: tuple[ProviderVideo, ...]
    next_cursor: int
    has_more: bool


class DouyinProvider(Protocol):
    """Source boundary used by short-drama services.

    Business services must depend on this protocol instead of a crawler or an
    HTTP implementation. Additional providers can implement the same methods
    without changing the episode pipeline.
    """

    async def resolve_account(self, homepage_url: str) -> ProviderAccount:
        """Resolve a user homepage URL into the provider's stable account ID."""

    async def get_user_profile(self, account: ProviderAccount) -> ProviderProfile:
        """Retrieve the latest display data for an account."""

    async def get_latest_videos(
        self,
        account: ProviderAccount,
        limit: int = 20,
    ) -> list[ProviderVideo]:
        """Retrieve the latest published videos in newest-first order."""

    async def get_video_page(
        self,
        account: ProviderAccount,
        *,
        cursor: int,
        limit: int,
    ) -> ProviderVideoPage:
        """Retrieve one cursor-addressable page without changing latest-video semantics."""

    async def aclose(self) -> None:
        """Release provider resources when the application stops."""
