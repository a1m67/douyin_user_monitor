"""Persist notification outcomes without affecting already-committed episodes."""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Iterable

from douyin_user_monitor.notifiers.base import EpisodeNotification, Notifier
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import EpisodeUpdate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotificationDispatchResult:
    channel: str
    success: bool
    error: str | None


class NotificationDispatcher:
    def __init__(self, *, repository: ShortDramaRepository, notifiers: Iterable[Notifier] = (),
                 poll_seconds: float = 5.0, max_attempts: int = 8,
                 max_backoff_seconds: int = 3600, claim_timeout_seconds: int = 300) -> None:
        self._repository = repository
        self._notifiers = tuple(notifiers)
        self._by_channel = {notifier.channel: notifier for notifier in self._notifiers}
        self._poll_seconds = max(0.1, poll_seconds)
        self._max_attempts = max(1, max_attempts)
        self._max_backoff_seconds = max(1, max_backoff_seconds)
        self._claim_timeout_seconds = max(1, claim_timeout_seconds)
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._last_success: str | None = None
        self._last_error: str | None = None

    @property
    def enabled_channels(self) -> tuple[str, ...]:
        return tuple(notifier.channel for notifier in self._notifiers)

    async def dispatch(self, update: EpisodeUpdate) -> tuple[NotificationDispatchResult, ...]:
        notification = EpisodeNotification.from_update(update)
        payload = {
            "show_id": notification.show_id, "episode_id": notification.episode_id,
            "show_title": notification.show_title, "season_number": notification.season_number,
            "episode_number": notification.episode_number, "account_nickname": notification.account_nickname,
            "published_at": notification.published_at, "video_url": notification.video_url,
            "cover_url": notification.cover_url,
        }
        queued_channels = []
        for channel in self.enabled_channels:
            self._repository.enqueue_notification_delivery(
                show_id=notification.show_id, episode_id=notification.episode_id,
                channel=channel, payload=payload,
            )
            queued_channels.append(channel)
        self.wake()
        deliveries = {
            item["channel"]: item
            for item in self._repository.list_notification_deliveries(episode_id=notification.episode_id)
        }
        return tuple(
            NotificationDispatchResult(
                channel,
                deliveries[channel]["status"] == "sent",
                deliveries[channel].get("last_error"),
            )
            for channel in queued_channels
        )

    def wake(self) -> None:
        self._wake.set()

    def health_status(self) -> dict[str, object]:
        return {
            "running": self._worker is not None and not self._worker.done(),
            "last_success": self._last_success,
            "last_error": self._last_error,
        }

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._stop.clear()
            self._worker = asyncio.create_task(self._run(), name="notification-delivery-worker")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker is not None:
            await self._worker
            self._worker = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                await self.deliver_due()
            except Exception:  # noqa: BLE001
                logger.exception("notification worker iteration failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def deliver_due(self) -> int:
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(seconds=self._claim_timeout_seconds)).isoformat(timespec="seconds")
        claimed = await asyncio.to_thread(
            self._repository.claim_notification_deliveries,
            limit=max(1, len(self._notifiers) * 4), stale_before=stale,
            now=now.isoformat(timespec="seconds"),
        )
        delivered = 0
        for job in claimed:
            notifier = self._by_channel.get(job["channel"])
            if notifier is None:
                await asyncio.to_thread(
                    self._repository.fail_notification_delivery, job["id"],
                    error="channel disabled", next_attempt_at=now.isoformat(timespec="seconds"), dead=True,
                )
                continue
            try:
                notification = EpisodeNotification(**job["payload"])
                await notifier.send_episode_update(notification)
            except Exception as exc:  # noqa: BLE001
                attempt = int(job.get("attempt_count") or 0) + 1
                dead = attempt >= self._max_attempts
                delay = min(self._max_backoff_seconds, 2 ** max(0, attempt - 1))
                next_time = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
                await asyncio.to_thread(
                    self._repository.fail_notification_delivery, job["id"],
                    error=str(exc) or exc.__class__.__name__, next_attempt_at=next_time, dead=dead,
                )
                self._last_error = str(exc) or exc.__class__.__name__
            else:
                await asyncio.to_thread(self._repository.complete_notification_delivery, job["id"])
                delivered += 1
                self._last_success = datetime.now(timezone.utc).isoformat(timespec="seconds")
                self._last_error = None
        return delivered

    async def aclose(self) -> None:
        await self.stop()
        for notifier in self._notifiers:
            try:
                await notifier.aclose()
            except Exception:  # noqa: BLE001 - shutdown should release remaining channels
                logger.exception("Failed to close notifier channel=%s", notifier.channel)

    def _record(
        self,
        notification: EpisodeNotification,
        channel: str,
        *,
        success: bool,
        error: str | None,
    ) -> None:
        try:
            self._repository.record_notification(
                show_id=notification.show_id,
                episode_id=notification.episode_id,
                channel=channel,
                success=success,
                error=error,
            )
        except Exception:  # noqa: BLE001 - notification persistence cannot undo an episode
            logger.exception("Failed to persist notification channel=%s", channel)
