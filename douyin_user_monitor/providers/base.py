"""Stable, crawler-independent Douyin provider contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class PageResultState(str, Enum):
    SUCCESS = "success"
    END_OF_FEED = "end_of_feed"
    TRANSIENT_EMPTY = "transient_empty"


class DouyinPageError(RuntimeError):
    """Base class for provider page failures that must not advance history."""


class TransientEmptyPageError(DouyinPageError):
    pass


class MalformedResponseError(DouyinPageError):
    pass


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
    state: PageResultState | None = None

    def __post_init__(self) -> None:
        if self.state is not None:
            return
        state = PageResultState.SUCCESS
        if not self.videos:
            state = PageResultState.TRANSIENT_EMPTY if self.has_more else PageResultState.END_OF_FEED
        object.__setattr__(self, "state", state)


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
