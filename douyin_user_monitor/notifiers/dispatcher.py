"""Persist notification outcomes without affecting already-committed episodes."""
from __future__ import annotations

import logging
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
    def __init__(self, *, repository: ShortDramaRepository, notifiers: Iterable[Notifier] = ()) -> None:
        self._repository = repository
        self._notifiers = tuple(notifiers)

    @property
    def enabled_channels(self) -> tuple[str, ...]:
        return tuple(notifier.channel for notifier in self._notifiers)

    async def dispatch(self, update: EpisodeUpdate) -> tuple[NotificationDispatchResult, ...]:
        notification = EpisodeNotification.from_update(update)
        results: list[NotificationDispatchResult] = []
        for notifier in self._notifiers:
            try:
                await notifier.send_episode_update(notification)
            except Exception as exc:  # noqa: BLE001 - channel failures are recorded, never rolled back
                error = str(exc) or exc.__class__.__name__
                self._record(notification, notifier.channel, success=False, error=error)
                results.append(NotificationDispatchResult(notifier.channel, False, error))
                continue
            self._record(notification, notifier.channel, success=True, error=None)
            results.append(NotificationDispatchResult(notifier.channel, True, None))
        return tuple(results)

    async def aclose(self) -> None:
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
