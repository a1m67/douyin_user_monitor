"""Adapter from the bundled Douyin Web crawler to :mod:`providers.base`."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from douyin_user_monitor.providers.base import (
    ProviderAccount,
    ProviderProfile,
    ProviderVideo,
    ProviderVideoPage,
)
from douyin_user_monitor.video_text import build_video_text_metadata


class BuiltinCrawlerProtocol(Protocol):
    """Narrow portion of the legacy crawler used by this adapter."""

    async def get_sec_user_id(self, url: str) -> str:
        ...

    async def handler_user_profile(self, sec_user_id: str) -> dict[str, Any]:
        ...

    async def fetch_user_post_videos(
        self,
        sec_user_id: str,
        max_cursor: int,
        count: int,
    ) -> dict[str, Any]:
        ...

    async def aclose(self) -> None:
        ...


class BuiltinDouyinProvider:
    """Expose the existing process-local crawler through the stable provider API."""

    def __init__(self, crawler: BuiltinCrawlerProtocol):
        self._crawler = crawler

    async def resolve_account(self, homepage_url: str) -> ProviderAccount:
        raw_url = homepage_url.strip()
        if not raw_url:
            raise ValueError("抖音用户主页链接不能为空")
        sec_uid = await self._crawler.get_sec_user_id(raw_url)
        if not sec_uid.strip():
            raise ValueError("无法从主页链接解析 sec_uid")
        return ProviderAccount(id="", sec_uid=sec_uid.strip(), homepage_url=raw_url)

    async def get_user_profile(self, account: ProviderAccount) -> ProviderProfile:
        raw_profile = await self._crawler.handler_user_profile(account.sec_uid)
        if not isinstance(raw_profile, dict):
            raise ValueError("抖音用户资料格式无效")
        return ProviderProfile(
            nickname=_extract_nickname(raw_profile) or account.sec_uid[:12],
            avatar_url=_extract_avatar_url(raw_profile),
        )

    async def get_latest_videos(
        self,
        account: ProviderAccount,
        limit: int = 20,
    ) -> list[ProviderVideo]:
        page = await self.get_video_page(account, cursor=0, limit=limit)
        return list(page.videos)

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
        payload = await self._crawler.fetch_user_post_videos(
            account.sec_uid,
            max_cursor=cursor,
            count=limit,
        )
        raw_list = payload.get("aweme_list") if isinstance(payload, dict) else None
        if raw_list is None:
            return ProviderVideoPage(videos=(), next_cursor=cursor, has_more=False)
        if not isinstance(raw_list, list) or not all(isinstance(item, dict) for item in raw_list):
            raise ValueError("抖音作品列表格式无效")

        videos = [self._to_provider_video(item) for item in raw_list]
        videos = [video for video in videos if video is not None]
        next_cursor = _extract_next_cursor(payload, fallback=cursor)
        return ProviderVideoPage(
            videos=tuple(sorted(videos, key=lambda video: video.publish_time or "", reverse=True)),
            next_cursor=next_cursor,
            has_more=_extract_has_more(payload, current_cursor=cursor, next_cursor=next_cursor),
        )

    async def aclose(self) -> None:
        await self._crawler.aclose()

    def _to_provider_video(self, raw: Mapping[str, Any]) -> ProviderVideo | None:
        aweme_id = str(raw.get("aweme_id") or "").strip()
        if not aweme_id:
            return None
        description = str(raw.get("desc") or "").strip()
        text_metadata = build_video_text_metadata(raw, description=description)
        return ProviderVideo(
            aweme_id=aweme_id,
            description=description,
            hashtags=tuple(_extract_hashtags(raw)),
            publish_time=_to_iso_time(raw.get("create_time")),
            video_url=_extract_video_url(raw, aweme_id),
            cover_url=_extract_cover_url(raw),
            raw=dict(raw),
            display_title=text_metadata.display_title,
            text_sources=text_metadata.text_sources,
        )


def _extract_hashtags(raw: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    text_extra = raw.get("text_extra")
    if isinstance(text_extra, list):
        for item in text_extra:
            if not isinstance(item, Mapping):
                continue
            tag = str(item.get("hashtag_name") or "").strip().lstrip("#")
            if tag and tag not in result:
                result.append(tag)
    return result


def _extract_video_url(raw: Mapping[str, Any], aweme_id: str) -> str:
    for key in ("share_url", "aweme_url"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return f"https://www.douyin.com/video/{aweme_id}"


def _extract_cover_url(raw: Mapping[str, Any]) -> str | None:
    video = raw.get("video")
    if not isinstance(video, Mapping):
        return None
    for key in ("cover", "dynamic_cover", "origin_cover"):
        image = video.get(key)
        if not isinstance(image, Mapping):
            continue
        urls = image.get("url_list")
        if not isinstance(urls, list):
            continue
        for value in urls:
            url = str(value or "").strip()
            if url:
                return url
    return None


def _to_iso_time(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _extract_nickname(raw_profile: Mapping[str, Any]) -> str:
    for key in ("user", "user_info"):
        user = raw_profile.get(key)
        if not isinstance(user, Mapping):
            continue
        nickname = str(user.get("nickname") or "").strip()
        if nickname:
            return nickname
    return str(raw_profile.get("nickname") or "").strip()


def _extract_avatar_url(raw_profile: Mapping[str, Any]) -> str | None:
    for user_key in ("user", "user_info"):
        user = raw_profile.get(user_key)
        if not isinstance(user, Mapping):
            continue
        for image_key in (
            "avatar_thumb",
            "avatar_168x168",
            "avatar_medium",
            "avatar_300x300",
            "avatar_larger",
        ):
            image = user.get(image_key)
            if not isinstance(image, Mapping):
                continue
            urls = image.get("url_list")
            if not isinstance(urls, list):
                continue
            for value in urls:
                url = str(value or "").strip()
                if url:
                    return url
    return None


def _extract_next_cursor(payload: Mapping[str, Any], *, fallback: int) -> int:
    for key in ("next_cursor", "max_cursor", "cursor"):
        try:
            value = int(payload.get(key))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return fallback


def _extract_has_more(payload: Mapping[str, Any], *, current_cursor: int, next_cursor: int) -> bool:
    value = payload.get("has_more")
    if isinstance(value, bool):
        return value
    if value is not None:
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False
    return next_cursor > current_cursor
