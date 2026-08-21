"""Crawler-independent new-video to Show/Episode processing pipeline."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from douyin_user_monitor.monitor.history_sync import (
    HISTORY_SYNC_STATUS_PENDING,
    HISTORY_SYNC_STATUS_RUNNING,
)
from douyin_user_monitor.parsers.base import IGNORED, MATCHED, REVIEW
from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.regex import normalize_title
from douyin_user_monitor.providers.base import DouyinProvider, ProviderAccount, ProviderVideo
from douyin_user_monitor.repositories.sqlite import EpisodeWriteResult, ShortDramaRepository
from douyin_user_monitor.video_text import build_video_text_metadata
from douyin_user_monitor.ocr import OCRBackend, run_ocr

logger = logging.getLogger(__name__)


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
    ignored_videos: int
    new_episode_updates: tuple[EpisodeUpdate, ...]

    @property
    def matched_videos(self) -> int:
        return max(0, self.new_videos - self.review_videos - self.ignored_videos)

    @property
    def llm_calls(self) -> int:
        return 0


@dataclass(frozen=True)
class HistoryBackfillResult:
    account: dict[str, Any]
    fetched_videos: int
    new_videos: int
    duplicate_videos: int
    review_videos: int
    ignored_videos: int


@dataclass(frozen=True)
class ManualReviewResult:
    video: dict[str, Any]
    show: dict[str, Any]
    episode: dict[str, Any]
    update: EpisodeUpdate | None


@dataclass(frozen=True)
class ReparseVideoResult:
    video: dict[str, Any]
    status: str
    new_episode: bool


@dataclass(frozen=True)
class ReparseResult:
    account: dict[str, Any]
    requested_videos: int
    matched_videos: int
    review_videos: int
    ignored_videos: int
    new_episode_count: int


@dataclass(frozen=True)
class _VideoProcessingOutcome:
    status: str
    update: EpisodeUpdate | None


@dataclass(frozen=True)
class _VideoBatchResult:
    new_videos: int
    duplicate_videos: int
    review_videos: int
    ignored_videos: int
    updates: tuple[EpisodeUpdate, ...]


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
        incremental_fetch_limit: int = 30,
        history_backfill_page_size: int = 50,
        notify_on_initial_sync: bool = False,
        dispatcher: EpisodeUpdateDispatcher | None = None,
        default_check_interval_minutes: int = 10,
        ocr_backend: OCRBackend | None = None,
        ocr_timeout_seconds: float = 15,
    ) -> None:
        if not 0.0 <= auto_accept_confidence <= 1.0:
            raise ValueError("AUTO_ACCEPT_CONFIDENCE 必须在 0 到 1 之间")
        if initial_sync_limit <= 0:
            raise ValueError("INITIAL_SYNC_LIMIT 必须大于 0")
        if incremental_fetch_limit <= 0:
            raise ValueError("INCREMENTAL_FETCH_LIMIT 必须大于 0")
        if history_backfill_page_size <= 0:
            raise ValueError("HISTORY_BACKFILL_PAGE_SIZE 必须大于 0")
        if default_check_interval_minutes <= 0:
            raise ValueError("CHECK_INTERVAL_MINUTES 必须大于 0")
        self._repository = repository
        self._provider = provider
        self._parser = parser or EpisodeParser()
        self._auto_accept_confidence = auto_accept_confidence
        self._initial_sync_limit = initial_sync_limit
        self._incremental_fetch_limit = incremental_fetch_limit
        self._history_backfill_page_size = history_backfill_page_size
        self._notify_on_initial_sync = notify_on_initial_sync
        self._dispatcher = dispatcher
        self._default_check_interval_minutes = default_check_interval_minutes
        self._ocr_backend = ocr_backend
        self._ocr_timeout_seconds = ocr_timeout_seconds

    async def add_account(
        self,
        homepage_url: str,
        *,
        check_interval_minutes: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        provider_account = await self._provider.resolve_account(homepage_url)
        existing = self._repository.get_account_by_sec_uid(provider_account.sec_uid)
        if existing is not None:
            return existing, False
        profile = await self._provider.get_user_profile(provider_account)
        account = self._repository.create_account(
            sec_uid=provider_account.sec_uid,
            nickname=_usable_nickname(profile.nickname, provider_account.sec_uid),
            homepage_url=provider_account.homepage_url or homepage_url,
            check_interval_minutes=check_interval_minutes or self._default_check_interval_minutes,
        )
        return account, True

    async def refresh_account_profile(self, account_id: str) -> dict[str, Any]:
        account = self._require_account(account_id)
        profile = await self._provider.get_user_profile(_provider_account(account))
        return self._repository.update_account(
            account_id,
            nickname=_usable_nickname(profile.nickname, str(account["sec_uid"])),
        )

    async def sync_account(self, account_id: str, *, limit: int | None = None) -> SyncResult:
        account = self._require_account(account_id)
        initial_sync = not bool(account["initial_sync_completed"])
        fetch_limit = limit or (
            self._initial_sync_limit if initial_sync else self._incremental_fetch_limit
        )
        logger.info("[account] start check nickname=%s account_id=%s", account["nickname"], account_id)
        videos = await self._provider.get_latest_videos(_provider_account(account), limit=fetch_limit)
        logger.info("[fetch] account_id=%s fetched_videos=%s", account_id, len(videos))
        batch = await self._ingest_videos(
            account=account,
            provider_videos=videos,
            collect_updates=not initial_sync or self._notify_on_initial_sync,
            create_update_events=not initial_sync,
        )
        logger.info(
            "[dedupe] account_id=%s new_videos=%s duplicate_videos=%s review_videos=%s ignored_videos=%s",
            account_id,
            batch.new_videos,
            batch.duplicate_videos,
            batch.review_videos,
            batch.ignored_videos,
        )

        if initial_sync:
            account = self._repository.complete_initial_sync(account_id)
        else:
            account = self._require_account(account_id)
        if self._dispatcher is not None:
            for update in batch.updates:
                await self._dispatcher.dispatch(update)
        logger.info("[account] complete account_id=%s new_episode_updates=%s", account_id, len(batch.updates))
        return SyncResult(
            account=account,
            initial_sync=initial_sync,
            fetched_videos=len(videos),
            new_videos=batch.new_videos,
            duplicate_videos=batch.duplicate_videos,
            review_videos=batch.review_videos,
            ignored_videos=batch.ignored_videos,
            new_episode_updates=batch.updates,
        )

    def start_history_backfill(self, account_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        return self._repository.start_history_backfill(account_id)

    def pause_history_backfill(self, account_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        return self._repository.pause_history_backfill(account_id)

    def resume_history_backfill(self, account_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        return self._repository.resume_history_backfill(account_id)

    async def run_history_backfill_page(
        self,
        account_id: str,
        *,
        seen_cursors: set[int] | None = None,
        mark_failed_on_error: bool = True,
    ) -> HistoryBackfillResult:
        """Process exactly one persisted cursor page without dispatching notifications."""
        account = self._require_account(account_id)
        history = account["history_sync"]
        if history["status"] not in {HISTORY_SYNC_STATUS_PENDING, HISTORY_SYNC_STATUS_RUNNING}:
            raise ValueError("请先开始或继续历史补全")
        if not history["has_more"]:
            raise ValueError("历史补全已完成，请重新开始")

        cursor = int(history["next_cursor"])
        try:
            page = await self._provider.get_video_page(
                _provider_account(account),
                cursor=cursor,
                limit=self._history_backfill_page_size,
            )
            next_cursor = int(page.next_cursor)
            effective_has_more = bool(page.has_more and page.videos)
            known_cursors = set(history.get("cursor_history") or ())
            if seen_cursors is not None:
                known_cursors.update(seen_cursors)
            if effective_has_more and next_cursor == cursor:
                raise ValueError("历史分页游标重复")
            if effective_has_more and next_cursor in known_cursors:
                raise ValueError("检测到历史分页游标循环")

            batch = await self._ingest_videos(
                account=account,
                provider_videos=page.videos,
                collect_updates=False,
                create_update_events=False,
            )
            cursor_history = list(history.get("cursor_history") or [cursor])
            if next_cursor not in cursor_history:
                cursor_history.append(next_cursor)
            account = self._repository.advance_history_backfill_page(
                account_id,
                expected_cursor=cursor,
                next_cursor=next_cursor,
                has_more=effective_has_more,
                scanned_count=len(page.videos),
                new_video_count=batch.new_videos,
                cursor_history=cursor_history,
            )
        except Exception as exc:
            if mark_failed_on_error:
                self._repository.fail_history_backfill(
                    account_id,
                    error=_safe_history_error(exc),
                )
                raise RuntimeError("历史补全失败，请恢复后重试") from exc
            raise

        logger.info(
            "[history] account_id=%s cursor=%s fetched_videos=%s new_videos=%s status=%s",
            account_id,
            cursor,
            len(page.videos),
            batch.new_videos,
            account["history_sync_status"],
        )
        return HistoryBackfillResult(
            account=account,
            fetched_videos=len(page.videos),
            new_videos=batch.new_videos,
            duplicate_videos=batch.duplicate_videos,
            review_videos=batch.review_videos,
            ignored_videos=batch.ignored_videos,
        )

    def confirm_review(
        self,
        video_id: int,
        *,
        episode_number: int,
        season_number: int = 1,
        show_id: int | None = None,
        new_show_title: str | None = None,
        learn_alias: bool = False,
    ) -> ManualReviewResult:
        if episode_number < 0:
            raise ValueError("集数不能小于 0")
        if season_number < 1:
            raise ValueError("季数不能小于 1")
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
            show = self._repository.get_show_by_title_or_alias(title)
            if show is None:
                show = self._repository.create_show(
                    title=title,
                    normalized_title=normalized,
                    aliases=[],
                )
        if show["is_ignored"]:
            raise ValueError("短剧已永久忽略，请先恢复追踪")
        candidate_alias = str(video.get("show_title_candidate") or "").strip()
        if learn_alias and candidate_alias:
            show = self._repository.add_show_alias(int(show["id"]), candidate_alias)

        write = self._repository.record_episode_source(
            show_id=int(show["id"]),
            episode_number=episode_number,
            season_number=season_number,
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
            parsed_season_number=season_number,
            parsed_episode_number=episode_number,
            parser_method="manual_review",
            classification_status=MATCHED,
            parser_reason="manual_review",
            show_title_candidate=str(show["title"]),
            season_candidate=season_number,
            episode_candidate=episode_number,
            content_type="episode",
        )
        update = _episode_update_if_new(show, write, processed_video, account)
        return ManualReviewResult(
            video=processed_video,
            show=show,
            episode=write.episode,
            update=update,
        )

    def ignore_review(self, video_id: int) -> dict[str, Any]:
        video = self._repository.get_video(video_id)
        if video is None:
            raise KeyError("视频不存在")
        if video["classification_status"] != REVIEW:
            raise ValueError("视频不在人工审核队列")
        self._repository.ignore_review_videos([video_id])
        ignored = self._repository.get_video(video_id)
        if ignored is None:
            raise RuntimeError("忽略视频后无法读取记录")
        return ignored

    def ignore_reviews(self, video_ids: list[int]) -> int:
        return self._repository.ignore_review_videos(video_ids)

    def ignore_show(self, show_id: int, *, reason: str | None = None) -> dict[str, Any]:
        return self._repository.ignore_show(show_id, reason=reason)

    def restore_show(self, show_id: int) -> dict[str, Any]:
        return self._repository.restore_show(show_id)

    def remove_episode(self, show_id: int, episode_id: int) -> dict[str, Any]:
        return self._repository.remove_episode(show_id, episode_id)

    def remove_episode_source(
        self, show_id: int, episode_id: int, source_id: int
    ) -> dict[str, Any]:
        return self._repository.remove_episode_source(show_id, episode_id, source_id)

    def reparse_video(self, video_id: int) -> ReparseVideoResult:
        """Reparse one existing ignored/review video without notification."""
        video = self._repository.get_video(video_id)
        if video is None:
            raise KeyError("视频不存在")
        if video["classification_status"] == MATCHED:
            raise ValueError("已匹配视频无需重新解析")
        account = self._require_account(str(video["account_id"]))
        outcome = self._process_video(account=account, video=video)
        stored = self._repository.get_video(video_id)
        if stored is None:
            raise RuntimeError("重新解析后无法读取视频记录")
        return ReparseVideoResult(
            video=stored,
            status=outcome.status,
            new_episode=outcome.update is not None,
        )

    def reparse_account(
        self,
        account_id: str,
        *,
        scope: str = "legacy_ignored",
    ) -> ReparseResult:
        """Reparse persisted videos in chronological order, never dispatching."""
        account = self._require_account(account_id)
        videos = self._repository.list_reparse_videos(account_id, scope=scope)
        matched_videos = 0
        review_videos = 0
        ignored_videos = 0
        new_episode_count = 0
        for video in videos:
            outcome = self._process_video(account=account, video=video)
            if outcome.status == MATCHED:
                matched_videos += 1
            elif outcome.status == REVIEW:
                review_videos += 1
            else:
                ignored_videos += 1
            if outcome.update is not None:
                new_episode_count += 1
        return ReparseResult(
            account=self._require_account(account_id),
            requested_videos=len(videos),
            matched_videos=matched_videos,
            review_videos=review_videos,
            ignored_videos=ignored_videos,
            new_episode_count=new_episode_count,
        )

    async def _ingest_videos(
        self,
        *,
        account: dict[str, Any],
        provider_videos: Sequence[ProviderVideo],
        collect_updates: bool,
        create_update_events: bool,
    ) -> _VideoBatchResult:
        new_videos = 0
        duplicate_videos = 0
        review_videos = 0
        ignored_videos = 0
        updates: list[EpisodeUpdate] = []

        # Oldest first keeps episode creation and optional notifications ordered.
        for provider_video in sorted(provider_videos, key=lambda item: item.publish_time or ""):
            text_metadata = build_video_text_metadata(
                provider_video.raw,
                description=provider_video.description,
                display_title=provider_video.display_title,
                text_sources=provider_video.text_sources,
            )
            video, created = self._repository.create_video(
                aweme_id=provider_video.aweme_id,
                account_id=str(account["id"]),
                description=provider_video.description,
                hashtags=provider_video.hashtags,
                publish_time=provider_video.publish_time,
                video_url=provider_video.video_url,
                cover_url=provider_video.cover_url,
                raw=provider_video.raw,
                display_title=text_metadata.display_title,
                text_sources=text_metadata.text_sources,
            )
            if not created:
                duplicate_videos += 1
                refreshed_video, metadata_enhanced = self._repository.refresh_video_metadata(
                    int(video["id"]),
                    description=provider_video.description,
                    video_url=provider_video.video_url,
                    cover_url=provider_video.cover_url,
                    raw=provider_video.raw,
                    display_title=text_metadata.display_title,
                    text_sources=text_metadata.text_sources,
                )
                if (
                    not metadata_enhanced
                    or not _metadata_refresh_can_reparse(video)
                    or not _metadata_refresh_changes_parser_inputs(video, refreshed_video)
                ):
                    continue
                # Rehydration recovers historical records. It may create an
                # EpisodeSource, but it never enters the notification batch.
                outcome = await asyncio.to_thread(
                    self._process_video,
                    account=account,
                    video=refreshed_video,
                    create_update_event=False,
                )
                if outcome.status == REVIEW:
                    review_videos += 1
                elif outcome.status == IGNORED:
                    ignored_videos += 1
                continue
            new_videos += 1
            outcome = await asyncio.to_thread(
                self._process_video,
                account=account,
                video=video,
                create_update_event=create_update_events,
            )
            if outcome.status == REVIEW:
                review_videos += 1
            elif outcome.status == IGNORED:
                ignored_videos += 1
            if collect_updates and outcome.update is not None:
                updates.append(outcome.update)
        return _VideoBatchResult(
            new_videos=new_videos,
            duplicate_videos=duplicate_videos,
            review_videos=review_videos,
            ignored_videos=ignored_videos,
            updates=tuple(updates),
        )

    def _process_video(
        self,
        *,
        account: dict[str, Any],
        video: dict[str, Any],
        create_update_event: bool = False,
    ) -> _VideoProcessingOutcome:
        text_metadata = build_video_text_metadata(
            video.get("raw_json"),
            description=str(video.get("description") or ""),
            display_title=video.get("display_title"),
            text_sources=video.get("text_sources"),
        )
        if (
            video.get("display_title") != text_metadata.display_title
            or video.get("text_sources") != text_metadata.text_sources
        ):
            video = self._repository.update_video_text_metadata(
                int(video["id"]),
                display_title=text_metadata.display_title,
                text_sources=text_metadata.text_sources,
            )
        parsed = self._parser.parse(
            display_title=text_metadata.display_title or "",
            description=str(video.get("description") or ""),
            hashtags=video.get("hashtags") or (),
            account_nickname=str(account["nickname"]),
            known_shows=self._repository.list_show_candidates(),
            recent_account_videos=self._repository.list_recent_account_videos(
                str(account["id"]), limit=20, exclude_video_id=int(video["id"])
            ),
            recent_account_matches=self._repository.list_recent_account_matches(
                str(account["id"]), limit=20, exclude_video_id=int(video["id"])
            ),
            account_show_candidates=self._repository.list_account_show_candidates(
                str(account["id"]), limit=20
            ),
            text_sources=text_metadata.text_sources,
        )
        if parsed.status == REVIEW and video.get("cover_url") and (
            self._ocr_backend is not None or video.get("ocr_processed_at")
        ):
            ocr_text = str(video.get("ocr_text") or "").strip()
            if not video.get("ocr_processed_at") and self._ocr_backend is not None:
                try:
                    ocr_result = run_ocr(self._ocr_backend, str(video["cover_url"]), self._ocr_timeout_seconds)
                    video = self._repository.save_video_ocr(int(video["id"]), text=ocr_result.text, confidence=ocr_result.confidence)
                    ocr_text = ocr_result.text if ocr_result.confidence >= 0.8 else ""
                except Exception:
                    video = self._repository.save_video_ocr(int(video["id"]), text=None, confidence=None)
                    ocr_text = ""
            elif float(video.get("ocr_confidence") or 0) < 0.8:
                ocr_text = ""
            if ocr_text:
                ocr_parsed = self._parser.parse(
                    display_title=ocr_text, description=ocr_text, hashtags=(),
                    account_nickname=str(account["nickname"]),
                    known_shows=self._repository.list_show_candidates(),
                    recent_account_videos=(), recent_account_matches=(),
                    account_show_candidates=self._repository.list_account_show_candidates(str(account["id"]), limit=20),
                    text_sources={"ocr": ocr_text},
                )
                if ocr_parsed.status == MATCHED and ocr_parsed.show_title and ocr_parsed.episode_number is not None:
                    parsed = ocr_parsed
        candidate_title = parsed.show_title_candidate or parsed.show_title
        ignored_show = (
            self._repository.get_show_by_title_or_alias(candidate_title)
            if candidate_title
            else None
        )
        if ignored_show is not None and ignored_show["is_ignored"]:
            self._store_ignored_show_video(
                video=video,
                parsed=parsed,
                ignored_show=ignored_show,
                candidate_title=candidate_title,
            )
            return _VideoProcessingOutcome(status=IGNORED, update=None)
        if parsed.status == IGNORED:
            logger.info(
                "[parse] ignored aweme_id=%s reason=%s method=%s",
                video["aweme_id"],
                parsed.reason,
                parsed.method,
            )
            self._repository.update_video_processing(
                int(video["id"]),
                is_processed=True,
                needs_review=False,
                parser_confidence=parsed.confidence,
                parsed_show_title=parsed.show_title,
                parsed_season_number=parsed.season_number,
                parsed_episode_number=parsed.episode_number,
                parser_method=parsed.method,
                classification_status=IGNORED,
                parser_reason=parsed.reason,
                show_title_candidate=candidate_title,
                season_candidate=parsed.season_number,
                episode_candidate=parsed.episode_candidate,
                content_type=parsed.content_type,
                parser_evidence=parsed.evidence,
                llm_raw_result=parsed.llm_raw_result,
            )
            return _VideoProcessingOutcome(status=IGNORED, update=None)

        if (
            parsed.status == REVIEW
            or parsed.show_title is None
            or parsed.episode_number is None
            or parsed.confidence < self._auto_accept_confidence
        ):
            reason = parsed.reason
            if parsed.status == MATCHED and parsed.confidence < self._auto_accept_confidence:
                reason = "matched_below_auto_accept_confidence"
            logger.info(
                "[parse] needs_review aweme_id=%s reason=%s method=%s confidence=%.2f",
                video["aweme_id"],
                reason,
                parsed.method,
                parsed.confidence,
            )
            self._repository.update_video_processing(
                int(video["id"]),
                is_processed=False,
                needs_review=True,
                parser_confidence=parsed.confidence,
                parsed_show_title=parsed.show_title,
                parsed_season_number=parsed.season_number,
                parsed_episode_number=parsed.episode_number,
                parser_method=parsed.method,
                classification_status=REVIEW,
                parser_reason=reason,
                show_title_candidate=candidate_title,
                season_candidate=parsed.season_number,
                episode_candidate=parsed.episode_candidate,
                content_type=parsed.content_type,
                parser_evidence=parsed.evidence,
                llm_raw_result=parsed.llm_raw_result,
            )
            return _VideoProcessingOutcome(status=REVIEW, update=None)

        logger.info(
            "[parse] accepted aweme_id=%s title=%s episode=%s method=%s confidence=%.2f",
            video["aweme_id"],
            parsed.show_title,
            parsed.episode_number,
            parsed.method,
            parsed.confidence,
        )
        show = self._find_or_create_show(parsed.show_title, parsed.matched_show_id)
        if show["is_ignored"]:
            self._store_ignored_show_video(
                video=video,
                parsed=parsed,
                ignored_show=show,
                candidate_title=candidate_title,
            )
            return _VideoProcessingOutcome(status=IGNORED, update=None)
        try:
            write = self._repository.record_episode_source(
                show_id=int(show["id"]),
                episode_number=parsed.episode_number,
                season_number=parsed.season_number,
                video_id=int(video["id"]),
                account_id=str(account["id"]),
                published_at=video.get("publish_time"),
                create_update_event=create_update_event,
            )
        except ValueError as exc:
            # A user may ignore the Show between matching and the write
            # transaction. Treat that race as the requested ignored state.
            refreshed_show = self._repository.get_show(int(show["id"]))
            if refreshed_show is None or not refreshed_show["is_ignored"]:
                raise
            logger.info(
                "[parse] show ignored during archive aweme_id=%s show_id=%s error=%s",
                video["aweme_id"],
                show["id"],
                exc,
            )
            self._store_ignored_show_video(
                video=video,
                parsed=parsed,
                ignored_show=refreshed_show,
                candidate_title=candidate_title,
            )
            return _VideoProcessingOutcome(status=IGNORED, update=None)
        processed_video = self._repository.update_video_processing(
            int(video["id"]),
            is_processed=True,
            needs_review=False,
            parser_confidence=parsed.confidence,
            parsed_show_title=str(show["title"]),
            parsed_season_number=parsed.season_number,
            parsed_episode_number=parsed.episode_number,
            parser_method=parsed.method,
            classification_status=MATCHED,
            parser_reason=parsed.reason,
            show_title_candidate=candidate_title,
            season_candidate=parsed.season_number,
            episode_candidate=parsed.episode_candidate,
            content_type=parsed.content_type,
            parser_evidence=parsed.evidence,
            llm_raw_result=parsed.llm_raw_result,
        )
        return _VideoProcessingOutcome(
            status=MATCHED,
            update=_episode_update_if_new(show, write, processed_video, account),
        )

    def _find_or_create_show(self, title: str, matched_show_id: int | None) -> dict[str, Any]:
        if matched_show_id is not None:
            existing = self._repository.get_show(matched_show_id)
            if existing is not None:
                return existing
        normalized = normalize_title(title)
        existing = self._repository.get_show_by_title_or_alias(title)
        if existing is not None:
            logger.info("[show] matched show_id=%s", existing["id"])
            return existing
        created = self._repository.create_show(title=title, normalized_title=normalized, aliases=[])
        logger.info("[show] created show_id=%s", created["id"])
        return created

    def _store_ignored_show_video(
        self,
        *,
        video: dict[str, Any],
        parsed: Any,
        ignored_show: dict[str, Any],
        candidate_title: str | None,
    ) -> None:
        logger.info(
            "[parse] ignored_show aweme_id=%s show_id=%s",
            video["aweme_id"],
            ignored_show["id"],
        )
        self._repository.update_video_processing(
            int(video["id"]),
            is_processed=True,
            needs_review=False,
            parser_confidence=parsed.confidence,
            parsed_show_title=str(ignored_show["title"]),
            parsed_season_number=parsed.season_number,
            parsed_episode_number=parsed.episode_number,
            parser_method=parsed.method,
            classification_status=IGNORED,
            parser_reason="ignored_show",
            show_title_candidate=candidate_title or str(ignored_show["title"]),
            season_candidate=parsed.season_number,
            episode_candidate=parsed.episode_candidate,
            content_type=parsed.content_type,
            parser_evidence=parsed.evidence,
            llm_raw_result=parsed.llm_raw_result,
        )

    def _require_account(self, account_id: str) -> dict[str, Any]:
        account = self._repository.get_account(account_id)
        if account is None:
            raise KeyError("账号不存在")
        return account


def _metadata_refresh_can_reparse(video: dict[str, Any]) -> bool:
    """Only rehydrate unresolved historical records during normal syncs."""
    status = str(video.get("classification_status") or "")
    if status == MATCHED:
        return False
    return status in {IGNORED, REVIEW} or str(video.get("parser_reason") or "") == "legacy_ignored"


def _metadata_refresh_changes_parser_inputs(
    previous: dict[str, Any],
    refreshed: dict[str, Any],
) -> bool:
    """Avoid reprocessing when only artwork, playback links, or opaque raw data changed."""
    return (
        previous.get("description") != refreshed.get("description")
        or previous.get("text_sources") != refreshed.get("text_sources")
    )


def _provider_account(account: dict[str, Any]) -> ProviderAccount:
    return ProviderAccount(
        id=str(account["id"]),
        sec_uid=str(account["sec_uid"]),
        homepage_url=str(account.get("homepage_url") or ""),
    )


def _safe_history_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return re.sub(
        r"(?i)\b(cookie|token|authorization|webhook)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2***",
        text,
    )[:2000]


def _usable_nickname(value: str, sec_uid: str) -> str:
    nickname = str(value or "").strip()
    if nickname.casefold() in {"", "nan", "none", "null", "undefined", "n/a"}:
        return f"作者 {sec_uid[:12]}"
    return nickname


def _episode_update_if_new(
    show: dict[str, Any],
    write: EpisodeWriteResult,
    video: dict[str, Any],
    account: dict[str, Any],
) -> EpisodeUpdate | None:
    if not write.is_new_episode:
        logger.info("[episode] existing episode=%s", write.episode["episode_number"])
        return None
    logger.info("[episode] new episode=%s", write.episode["episode_number"])
    return EpisodeUpdate(show=show, episode=write.episode, video=video, account=account)
