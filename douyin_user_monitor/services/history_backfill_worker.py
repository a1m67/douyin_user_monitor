"""Background coordinator for throttled, resumable history backfills."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryBackfillWorkerConfig:
    max_concurrent_backfills: int = 1
    delay_min_seconds: float = 3.0
    delay_max_seconds: float = 6.0
    retry_delays_seconds: tuple[float, float] = (3.0, 8.0)
    poll_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.max_concurrent_backfills <= 0:
            raise ValueError("MAX_CONCURRENT_HISTORY_BACKFILLS 必须大于 0")
        if self.delay_min_seconds < 0 or self.delay_max_seconds < self.delay_min_seconds:
            raise ValueError("历史补全分页延迟范围无效")
        if len(self.retry_delays_seconds) != 2 or any(delay < 0 for delay in self.retry_delays_seconds):
            raise ValueError("历史补全重试延迟必须包含两个非负值")
        if self.poll_seconds <= 0:
            raise ValueError("history worker poll_seconds 必须大于 0")


class HistoryBackfillWorker:
    def __init__(
        self,
        *,
        repository: ShortDramaRepository,
        pipeline: ShortDramaPipeline,
        config: HistoryBackfillWorkerConfig,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._config = config
        self._sleep = sleep
        self._jitter = jitter
        self._semaphore = asyncio.Semaphore(config.max_concurrent_backfills)
        self._wake_event = asyncio.Event()
        self._coordinator_task: asyncio.Task[None] | None = None
        self._account_tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if self._coordinator_task is not None and not self._coordinator_task.done():
            return
        recovered = self._repository.recover_running_history_backfills()
        if recovered:
            logger.info("[history-worker] recovered_running=%s", recovered)
        self._coordinator_task = asyncio.create_task(
            self._loop(),
            name="short-drama-history-backfill-worker",
        )
        self.wake()

    async def stop(self) -> None:
        coordinator, self._coordinator_task = self._coordinator_task, None
        if coordinator is not None:
            coordinator.cancel()
        tasks = list(self._account_tasks.values())
        self._account_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*(tasks + ([coordinator] if coordinator else [])), return_exceptions=True)

    def wake(self) -> None:
        self._wake_event.set()

    def health_status(self) -> str:
        return (
            "ok"
            if self._coordinator_task is not None and not self._coordinator_task.done()
            else "stopped"
        )

    async def wait_until_idle(self, *, timeout: float = 5.0) -> None:
        async def wait() -> None:
            while self._repository.list_active_history_backfills() or any(
                not task.done() for task in self._account_tasks.values()
            ):
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), timeout=timeout)

    async def _loop(self) -> None:
        try:
            while True:
                self._collect_finished_tasks()
                for account in self._repository.list_active_history_backfills():
                    account_id = str(account["id"])
                    if account_id in self._account_tasks:
                        continue
                    self._account_tasks[account_id] = asyncio.create_task(
                        self._run_account(account_id),
                        name=f"history-backfill-{account_id}",
                    )
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self._config.poll_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    def _collect_finished_tasks(self) -> None:
        for account_id, task in list(self._account_tasks.items()):
            if not task.done():
                continue
            self._account_tasks.pop(account_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("[history-worker] unexpected account failure account_id=%s", account_id)

    async def _run_account(self, account_id: str) -> None:
        async with self._semaphore:
            account = self._repository.mark_history_backfill_running(account_id)
            seen_cursors = set(account["history_sync"].get("cursor_history") or ())
            while True:
                account = self._repository.get_account(account_id)
                if account is None:
                    return
                history = account["history_sync"]
                if history["status"] == "pending":
                    account = self._repository.mark_history_backfill_running(account_id)
                    history = account["history_sync"]
                if history["status"] != "running" or not history["has_more"]:
                    return

                succeeded = False
                for attempt in range(3):
                    current = self._repository.get_account(account_id)
                    if current is None or current["history_sync_status"] != "running":
                        return
                    try:
                        result = await self._pipeline.run_history_backfill_page(
                            account_id,
                            seen_cursors=seen_cursors,
                            mark_failed_on_error=False,
                        )
                        succeeded = True
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - provider/page errors are retried
                        if attempt == 2:
                            current = self._repository.get_account(account_id)
                            if current is None or current["history_sync_status"] != "running":
                                return
                            error = _safe_error(exc)
                            self._repository.fail_history_backfill(account_id, error=error)
                            logger.warning(
                                "[history-worker] failed account_id=%s cursor=%s reason=%s",
                                account_id,
                                history["next_cursor"],
                                error,
                            )
                            return
                        await self._sleep(self._config.retry_delays_seconds[attempt])
                if not succeeded:
                    return

                history = result.account["history_sync"]
                seen_cursors.update(history.get("cursor_history") or ())
                if history["status"] != "running" or not history["has_more"]:
                    return
                await self._sleep(
                    self._jitter(
                        self._config.delay_min_seconds,
                        self._config.delay_max_seconds,
                    )
                )


def _safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return re.sub(
        r"(?i)\b(cookie|token|authorization|webhook)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2***",
        text,
    )[:2000]
