"""Deterministic provider for tests and local business-flow verification."""
from __future__ import annotations

from collections.abc import Mapping

from douyin_user_monitor.providers.base import (
    ProviderAccount,
    ProviderProfile,
    ProviderVideo,
)


class FakeDouyinProvider:
    def __init__(
        self,
        *,
        accounts_by_url: Mapping[str, ProviderAccount] | None = None,
        profiles_by_sec_uid: Mapping[str, ProviderProfile] | None = None,
        videos_by_sec_uid: Mapping[str, list[ProviderVideo]] | None = None,
    ) -> None:
        self.accounts_by_url = dict(accounts_by_url or {})
        self.profiles_by_sec_uid = dict(profiles_by_sec_uid or {})
        self.videos_by_sec_uid = {
            sec_uid: list(videos)
            for sec_uid, videos in (videos_by_sec_uid or {}).items()
        }

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
        return list(self.videos_by_sec_uid.get(account.sec_uid, []))[:limit]

    async def aclose(self) -> None:
        return None
