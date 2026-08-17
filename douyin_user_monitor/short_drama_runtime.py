"""Composition root for the production AI short-drama tracker."""
from __future__ import annotations

from dataclasses import dataclass

from douyin_user_monitor.crawler.inprocess_client import InProcessDouyinClient
from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.notifiers.feishu import FeishuNotifier
from douyin_user_monitor.notifiers.telegram import TelegramNotifier
from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.llm import LLMParser, OpenAICompatibleLLMClient
from douyin_user_monitor.providers.builtin_douyin import BuiltinDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.services.history_backfill_worker import (
    HistoryBackfillWorker,
    HistoryBackfillWorkerConfig,
)
from douyin_user_monitor.services.scheduler import AccountScheduler, SchedulerConfig
from douyin_user_monitor.short_drama_settings import (
    ShortDramaSettings,
    load_cookie_header,
    load_short_drama_settings,
)


@dataclass
class ShortDramaRuntime:
    settings: ShortDramaSettings
    repository: ShortDramaRepository
    provider: BuiltinDouyinProvider
    dispatcher: NotificationDispatcher
    pipeline: ShortDramaPipeline
    scheduler: AccountScheduler
    history_backfill_worker: HistoryBackfillWorker

    async def start(self) -> None:
        await self.history_backfill_worker.start()
        await self.scheduler.start()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        await self.history_backfill_worker.stop()
        await self.dispatcher.aclose()
        await self.provider.aclose()


def build_short_drama_runtime(settings: ShortDramaSettings | None = None) -> ShortDramaRuntime:
    resolved_settings = settings or load_short_drama_settings()
    crawler = InProcessDouyinClient(
        resolved_settings.crawler_config_path,
        cookie_override=load_cookie_header(resolved_settings.cookie_file),
    )
    provider = BuiltinDouyinProvider(crawler)
    repository = ShortDramaRepository(
        resolved_settings.database_path,
        legacy_state_path=resolved_settings.project_root / "data" / "monitor_users.json",
    )
    notifiers = []
    if resolved_settings.telegram_bot_token and resolved_settings.telegram_chat_id:
        notifiers.append(
            TelegramNotifier(
                bot_token=resolved_settings.telegram_bot_token,
                chat_id=resolved_settings.telegram_chat_id,
            )
        )
    if resolved_settings.feishu_webhook_url:
        notifiers.append(FeishuNotifier(webhook_url=resolved_settings.feishu_webhook_url))
    dispatcher = NotificationDispatcher(repository=repository, notifiers=notifiers)
    llm_backend = None
    if resolved_settings.llm_enabled:
        llm_backend = LLMParser(
            OpenAICompatibleLLMClient(
                api_key=resolved_settings.llm_api_key,
                base_url=resolved_settings.llm_base_url,
                model=resolved_settings.llm_model,
                timeout_seconds=resolved_settings.llm_timeout_seconds,
            ),
            auto_accept_confidence=resolved_settings.llm_auto_accept_confidence,
        )
    parser = EpisodeParser(
        llm_backend=llm_backend,
        auto_accept_confidence=resolved_settings.auto_accept_confidence,
    )
    pipeline = ShortDramaPipeline(
        repository=repository,
        provider=provider,
        parser=parser,
        auto_accept_confidence=resolved_settings.auto_accept_confidence,
        initial_sync_limit=resolved_settings.initial_sync_limit,
        incremental_fetch_limit=resolved_settings.incremental_fetch_limit,
        history_backfill_page_size=resolved_settings.history_backfill_page_size,
        notify_on_initial_sync=resolved_settings.notify_on_initial_sync,
        dispatcher=dispatcher,
        default_check_interval_minutes=resolved_settings.check_interval_minutes,
    )
    scheduler = AccountScheduler(
        repository=repository,
        pipeline=pipeline,
        config=SchedulerConfig(
            default_check_interval_minutes=resolved_settings.check_interval_minutes,
            max_concurrent_checks=resolved_settings.max_concurrent_checks,
            max_backoff_minutes=resolved_settings.max_backoff_minutes,
            poll_seconds=resolved_settings.scheduler_poll_seconds,
        ),
    )
    history_backfill_worker = HistoryBackfillWorker(
        repository=repository,
        pipeline=pipeline,
        config=HistoryBackfillWorkerConfig(
            max_concurrent_backfills=resolved_settings.max_concurrent_history_backfills,
            delay_min_seconds=resolved_settings.history_backfill_delay_min_seconds,
            delay_max_seconds=resolved_settings.history_backfill_delay_max_seconds,
        ),
    )
    return ShortDramaRuntime(
        settings=resolved_settings,
        repository=repository,
        provider=provider,
        dispatcher=dispatcher,
        pipeline=pipeline,
        scheduler=scheduler,
        history_backfill_worker=history_backfill_worker,
    )
