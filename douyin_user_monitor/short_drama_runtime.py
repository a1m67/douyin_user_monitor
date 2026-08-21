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
from douyin_user_monitor.providers.base import ProviderAccount
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.services.crawler_circuit_breaker import CrawlerCircuitBreaker
from douyin_user_monitor.services.douyin_request_guard import DouyinRequestGuard
from douyin_user_monitor.services.history_backfill_worker import (
    HistoryBackfillWorker,
    HistoryBackfillWorkerConfig,
)
from douyin_user_monitor.services.scheduler import AccountScheduler, SchedulerConfig
from douyin_user_monitor.services.maintenance_worker import MaintenanceWorker, MaintenanceWorkerConfig
from douyin_user_monitor.services.cookie_manager import CookieManager
from douyin_user_monitor.short_drama_settings import (
    ShortDramaSettings,
    load_cookie_header,
    load_short_drama_settings,
)
from douyin_user_monitor.ocr import HttpOCRBackend


@dataclass
class ShortDramaRuntime:
    settings: ShortDramaSettings
    repository: ShortDramaRepository
    provider: BuiltinDouyinProvider
    dispatcher: NotificationDispatcher
    pipeline: ShortDramaPipeline
    scheduler: AccountScheduler
    history_backfill_worker: HistoryBackfillWorker
    cookie_manager: CookieManager
    maintenance_worker: MaintenanceWorker

    async def start(self) -> None:
        self.repository.prune_scan_runs(retention_days=self.settings.scan_run_retention_days)
        await self.dispatcher.start()
        await self.maintenance_worker.start()
        await self.history_backfill_worker.start()
        await self.scheduler.start()

    async def shutdown(self) -> None:
        await self.scheduler.stop()
        await self.history_backfill_worker.stop()
        await self.maintenance_worker.stop()
        await self.dispatcher.aclose()
        await self.provider.aclose()


def build_short_drama_runtime(settings: ShortDramaSettings | None = None) -> ShortDramaRuntime:
    resolved_settings = settings or load_short_drama_settings()
    crawler = InProcessDouyinClient(
        resolved_settings.crawler_config_path,
        cookie_override=load_cookie_header(resolved_settings.cookie_file),
    )
    circuit_breaker = CrawlerCircuitBreaker(
        enabled=resolved_settings.crawler_circuit_breaker_enabled,
        failure_threshold=resolved_settings.crawler_circuit_failure_threshold,
        open_minutes=resolved_settings.crawler_circuit_open_minutes,
    )
    request_guard = DouyinRequestGuard(
        circuit_breaker=circuit_breaker,
        max_concurrent_requests=resolved_settings.douyin_max_concurrent_requests,
        min_request_interval_seconds=resolved_settings.douyin_min_request_interval_seconds,
    )
    provider = BuiltinDouyinProvider(crawler, request_guard=request_guard)
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
    dispatcher = NotificationDispatcher(
        repository=repository, notifiers=notifiers,
        poll_seconds=resolved_settings.notification_poll_seconds,
        max_attempts=resolved_settings.notification_max_attempts,
        max_backoff_seconds=resolved_settings.notification_max_backoff_seconds,
        claim_timeout_seconds=resolved_settings.notification_claim_timeout_seconds,
    )
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
    ocr_backend = HttpOCRBackend(api_url=resolved_settings.ocr_api_url, api_key=resolved_settings.ocr_api_key, timeout_seconds=resolved_settings.ocr_timeout_seconds) if resolved_settings.ocr_enabled else None
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
        ocr_backend=ocr_backend,
        ocr_timeout_seconds=resolved_settings.ocr_timeout_seconds,
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
        request_guard=request_guard,
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
    async def test_cookie() -> dict[str, str]:
        account = next((item for item in repository.list_accounts() if item["enabled"]), None)
        if account is None:
            return {"status": "unknown", "reason": "没有可用的启用账号"}
        async with request_guard.force_requests():
            await provider.get_video_page(
                _runtime_provider_account(account), cursor=0, limit=1
            )
        return {"status": "healthy", "reason": "验证请求成功"}

    cookie_manager = CookieManager(
        resolved_settings.cookie_file,
        reload_cookie=crawler.set_cookie_override,
        test_cookie=test_cookie,
    )
    maintenance_worker = MaintenanceWorker(
        repository,
        MaintenanceWorkerConfig(
            enabled=resolved_settings.auto_maintenance_enabled,
            poll_seconds=resolved_settings.maintenance_poll_seconds,
            backup_interval_hours=resolved_settings.auto_backup_interval_hours,
            checkpoint_interval_hours=resolved_settings.wal_checkpoint_interval_hours,
            backup_retention_count=resolved_settings.backup_retention_count,
            scan_run_retention_days=resolved_settings.scan_run_retention_days,
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
        cookie_manager=cookie_manager,
        maintenance_worker=maintenance_worker,
    )


def _runtime_provider_account(account: dict[str, object]) -> ProviderAccount:
    return ProviderAccount(
        id=str(account["id"]),
        sec_uid=str(account["sec_uid"]),
        homepage_url=str(account.get("homepage_url") or ""),
    )
