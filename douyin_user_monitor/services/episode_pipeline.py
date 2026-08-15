"""Crawler-independent new-video to Show/Episode processing pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.regex import normalize_title
from douyin_user_monitor.providers.base import DouyinProvider, ProviderAccount, ProviderVideo
from douyin_user_monitor.repositories.sqlite import EpisodeWriteResult, ShortDramaRepository


@dataclass(frozen=True)
class EpisodeUpdate:
    """A newly discovered Show/Episode, ready for post-transaction notification."""

    show: dict[str, Any]
    episode: dict[str, Any]
    video: dict[str, Any]
    account: dict[str, Any]


@dataclass(frozen=True)
class SyncResult:
    account: dict[str, Any]
    initial_sync: bool
    fetched_videos: int
    new_videos: int
    duplicate_videos: int
    review_videos: int
    new_episode_updates: tuple[EpisodeUpdate, ...]


@dataclass(frozen=True)
class ManualReviewResult:
    video: dict[str, Any]
    show: dict[str, Any]
    episode: dict[str, Any]
    update: EpisodeUpdate | None


class EpisodeUpdateDispatcher(Protocol):
    async def dispatch(self, update: EpisodeUpdate) -> Any:
        ...


class ShortDramaPipeline:
    """Run short-drama business rules solely through :class:`DouyinProvider`."""

    def __init__(
        self,
        *,
        repository: ShortDramaRepository,
        provider: DouyinProvider,
        parser: EpisodeParser | None = None,
        auto_accept_confidence: float = 0.8,
        initial_sync_limit: int = 20,
        notify_on_initial_sync: bool = False,
        dispatcher: EpisodeUpdateDispatcher | None = None,
    ) -> None:
        if not 0.0 <= auto_accept_confidence <= 1.0:
            raise ValueError("AUTO_ACCEPT_CONFIDENCE 必须在 0 到 1 之间")
        if initial_sync_limit <= 0:
            raise ValueError("INITIAL_SYNC_LIMIT 必须大于 0")
        self._repository = repository
        self._provider = provider
        self._parser = parser or EpisodeParser()
        self._auto_accept_confidence = auto_accept_confidence
        self._initial_sync_limit = initial_sync_limit
        self._notify_on_initial_sync = notify_on_initial_sync
        self._dispatcher = dispatcher

    async def add_account(
        self,
        homepage_url: str,
        *,
        check_interval_minutes: int = 10,
    ) -> tuple[dict[str, Any], bool]:
        provider_account = await self._provider.resolve_account(homepage_url)
        existing = self._repository.get_account_by_sec_uid(provider_account.sec_uid)
        if existing is not None:
            return existing, False
        profile = await self._provider.get_user_profile(provider_account)
        account = self._repository.create_account(
            sec_uid=provider_account.sec_uid,
            nickname=profile.nickname,
            homepage_url=provider_account.homepage_url or homepage_url,
            check_interval_minutes=check_interval_minutes,
        )
        return account, True

    async def refresh_account_profile(self, account_id: str) -> dict[str, Any]:
        account = self._require_account(account_id)
        profile = await self._provider.get_user_profile(_provider_account(account))
        return self._repository.update_account(account_id, nickname=profile.nickname)

    async def sync_account(self, account_id: str, *, limit: int | None = None) -> SyncResult:
        account = self._require_account(account_id)
        fetch_limit = limit or self._initial_sync_limit
        videos = await self._provider.get_latest_videos(_provider_account(account), limit=fetch_limit)
        initial_sync = not bool(account["initial_sync_completed"])
        updates: list[EpisodeUpdate] = []
        new_videos = 0
        duplicate_videos = 0
        review_videos = 0

        # Process oldest first: when a first sync contains episodes 14-16 the
        # database receives a natural progression and later notifications stay ordered.
        for provider_video in sorted(videos, key=lambda item: item.publish_time or ""):
            video, created = self._repository.create_video(
                aweme_id=provider_video.aweme_id,
                account_id=account_id,
                description=provider_video.description,
                hashtags=provider_video.hashtags,
                publish_time=provider_video.publish_time,
                video_url=provider_video.video_url,
                cover_url=provider_video.cover_url,
                raw=provider_video.raw,
            )
            if not created:
                duplicate_videos += 1
                continue
            new_videos += 1
            update = self._process_new_video(account=account, video=video, provider_video=provider_video)
            if update is None:
                review_videos += 1
                continue
            if not initial_sync or self._notify_on_initial_sync:
                updates.append(update)

        if initial_sync:
            account = self._repository.complete_initial_sync(account_id)
        else:
            account = self._require_account(account_id)
        if self._dispatcher is not None:
            for update in updates:
                await self._dispatcher.dispatch(update)
        return SyncResult(
            account=account,
            initial_sync=initial_sync,
            fetched_videos=len(videos),
            new_videos=new_videos,
            duplicate_videos=duplicate_videos,
            review_videos=review_videos,
            new_episode_updates=tuple(updates),
        )

    def confirm_review(
        self,
        video_id: int,
        *,
        episode_number: int,
        show_id: int | None = None,
        new_show_title: str | None = None,
    ) -> ManualReviewResult:
        if episode_number <= 0:
            raise ValueError("集数必须大于 0")
        if (show_id is None) == (not bool((new_show_title or "").strip())):
            raise ValueError("请选择已有短剧，或提供一个新短剧名称（二者只能选一个）")
        video = self._repository.get_video(video_id)
        if video is None:
            raise KeyError("视频不存在")
        account = self._require_account(str(video["account_id"]))

        if show_id is not None:
            show = self._repository.get_show(show_id)
            if show is None:
                raise KeyError("短剧不存在")
        else:
            title = str(new_show_title or "").strip()
            normalized = normalize_title(title)
            if not normalized:
                raise ValueError("新短剧名称无效")
            show = self._repository.get_show_by_normalized_title(normalized)
            if show is None:
                show = self._repository.create_show(
                    title=title,
                    normalized_title=normalized,
                    aliases=[],
                )

        write = self._repository.record_episode_source(
            show_id=int(show["id"]),
            episode_number=episode_number,
            video_id=int(video["id"]),
            account_id=str(video["account_id"]),
            published_at=video.get("publish_time"),
        )
        processed_video = self._repository.update_video_processing(
            int(video["id"]),
            is_processed=True,
            needs_review=False,
            parser_confidence=1.0,
            parsed_show_title=str(show["title"]),
            parsed_episode_number=episode_number,
            parser_method="manual_review",
        )
        update = _episode_update_if_new(show, write, processed_video, account)
        return ManualReviewResult(
            video=processed_video,
            show=show,
            episode=write.episode,
            update=update,
        )

    def _process_new_video(
        self,
        *,
        account: dict[str, Any],
        video: dict[str, Any],
        provider_video: ProviderVideo,
    ) -> EpisodeUpdate | None:
        parsed = self._parser.parse(
            description=provider_video.description,
            hashtags=provider_video.hashtags,
            account_nickname=str(account["nickname"]),
            known_shows=self._repository.list_show_candidates(),
        )
        if (
            not parsed.is_episode
            or parsed.show_title is None
            or parsed.episode_number is None
            or parsed.confidence < self._auto_accept_confidence
        ):
            self._repository.update_video_processing(
                int(video["id"]),
                is_processed=False,
                needs_review=True,
                parser_confidence=parsed.confidence,
                parsed_show_title=parsed.show_title,
                parsed_episode_number=parsed.episode_number,
                parser_method=parsed.method,
            )
            return None

        show = self._find_or_create_show(parsed.show_title, parsed.matched_show_id)
        write = self._repository.record_episode_source(
            show_id=int(show["id"]),
            episode_number=parsed.episode_number,
            video_id=int(video["id"]),
            account_id=str(account["id"]),
            published_at=video.get("publish_time"),
        )
        processed_video = self._repository.update_video_processing(
            int(video["id"]),
            is_processed=True,
            needs_review=False,
            parser_confidence=parsed.confidence,
            parsed_show_title=str(show["title"]),
            parsed_episode_number=parsed.episode_number,
            parser_method=parsed.method,
        )
        return _episode_update_if_new(show, write, processed_video, account)

    def _find_or_create_show(self, title: str, matched_show_id: int | None) -> dict[str, Any]:
        if matched_show_id is not None:
            existing = self._repository.get_show(matched_show_id)
            if existing is not None:
                return existing
        normalized = normalize_title(title)
        existing = self._repository.get_show_by_normalized_title(normalized)
        if existing is not None:
            return existing
        return self._repository.create_show(title=title, normalized_title=normalized, aliases=[])

    def _require_account(self, account_id: str) -> dict[str, Any]:
        account = self._repository.get_account(account_id)
        if account is None:
            raise KeyError("账号不存在")
        return account


def _provider_account(account: dict[str, Any]) -> ProviderAccount:
    return ProviderAccount(
        id=str(account["id"]),
        sec_uid=str(account["sec_uid"]),
        homepage_url=str(account.get("homepage_url") or ""),
    )


def _episode_update_if_new(
    show: dict[str, Any],
    write: EpisodeWriteResult,
    video: dict[str, Any],
    account: dict[str, Any],
) -> EpisodeUpdate | None:
    if not write.is_new_episode:
        return None
    return EpisodeUpdate(show=show, episode=write.episode, video=video, account=account)
