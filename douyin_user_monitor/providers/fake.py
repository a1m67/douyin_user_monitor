"""Deterministic provider for tests and local business-flow verification."""
from __future__ import annotations

from collections.abc import Mapping

from douyin_user_monitor.providers.base import (
    ProviderAccount,
    ProviderProfile,
    ProviderVideo,
    ProviderVideoPage,
)


class FakeDouyinProvider:
    def __init__(
        self,
        *,
        accounts_by_url: Mapping[str, ProviderAccount] | None = None,
        profiles_by_sec_uid: Mapping[str, ProviderProfile] | None = None,
        videos_by_sec_uid: Mapping[str, list[ProviderVideo]] | None = None,
        video_pages_by_sec_uid: Mapping[str, Mapping[int, ProviderVideoPage]] | None = None,
    ) -> None:
        self.accounts_by_url = dict(accounts_by_url or {})
        self.profiles_by_sec_uid = dict(profiles_by_sec_uid or {})
        self.videos_by_sec_uid = {
            sec_uid: list(videos)
            for sec_uid, videos in (videos_by_sec_uid or {}).items()
        }
        self.video_pages_by_sec_uid = {
            sec_uid: dict(pages)
            for sec_uid, pages in (video_pages_by_sec_uid or {}).items()
        }
        self.latest_calls: list[tuple[str, int]] = []
        self.page_calls: list[tuple[str, int, int]] = []

    async def resolve_account(self, homepage_url: str) -> ProviderAccount:
        try:
            return self.accounts_by_url[homepage_url]
        except KeyError as exc:
            raise ValueError(f"Fake provider 未配置账号: {homepage_url}") from exc

    async def get_user_profile(self, account: ProviderAccount) -> ProviderProfile:
        return self.profiles_by_sec_uid.get(
            account.sec_uid,
            ProviderProfile(nickname=account.sec_uid[:12]),
        )

    async def get_latest_videos(
        self,
        account: ProviderAccount,
        limit: int = 20,
    ) -> list[ProviderVideo]:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        self.latest_calls.append((account.sec_uid, limit))
        return list(self.videos_by_sec_uid.get(account.sec_uid, []))[:limit]

    async def get_video_page(
        self,
        account: ProviderAccount,
        *,
        cursor: int,
        limit: int,
    ) -> ProviderVideoPage:
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if cursor < 0:
            raise ValueError("cursor 不能小于 0")
        self.page_calls.append((account.sec_uid, cursor, limit))
        configured_pages = self.video_pages_by_sec_uid.get(account.sec_uid)
        if configured_pages is not None and cursor in configured_pages:
            return configured_pages[cursor]

        videos = self.videos_by_sec_uid.get(account.sec_uid, [])
        page_videos = tuple(videos[cursor : cursor + limit])
        next_cursor = cursor + len(page_videos)
        return ProviderVideoPage(
            videos=page_videos,
            next_cursor=next_cursor,
            has_more=next_cursor < len(videos),
        )

    async def aclose(self) -> None:
        return None
