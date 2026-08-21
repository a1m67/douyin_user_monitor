from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from douyin_user_monitor.api.monitor import monitor_service, router as monitor_router
from douyin_user_monitor.short_drama_runtime import (
    ShortDramaRuntime,
    build_short_drama_runtime,
)
from douyin_user_monitor.short_drama_settings import load_short_drama_settings
from douyin_user_monitor.web.short_drama import create_short_drama_router
from douyin_user_monitor.web.auth import AppAuthConfig, AppAuthMiddleware, create_auth_router


class _RuntimeHolder:
    def __init__(self, runtime: ShortDramaRuntime | None = None) -> None:
        self._runtime = runtime

    def get(self) -> ShortDramaRuntime:
        if self._runtime is None:
            self._runtime = build_short_drama_runtime()
        return self._runtime


class _RuntimeAttributeProxy:
    def __init__(self, holder: _RuntimeHolder, attribute: str) -> None:
        self._holder = holder
        self._attribute = attribute

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._holder.get(), self._attribute)
        return getattr(target, name)


SHORT_DRAMA_RUNTIME: ShortDramaRuntime | None = None
_DEFAULT_RUNTIME_HOLDER = _RuntimeHolder()


def create_app(runtime: ShortDramaRuntime | None = None) -> FastAPI:
    holder = _RuntimeHolder(runtime)
    settings = runtime.settings if runtime is not None else load_short_drama_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_runtime = holder.get()
        application.state.short_drama_runtime = active_runtime
        await startup_monitor(active_runtime)
        try:
            yield
        finally:
            await shutdown_monitor(active_runtime)

    application = FastAPI(title="AI 短剧追更系统", version="1.0.0", lifespan=lifespan)
    auth_config = AppAuthConfig(
        enabled=getattr(settings, "app_auth_enabled", False),
        password=getattr(settings, "app_auth_password", ""),
        session_secret=getattr(settings, "app_session_secret", ""),
        session_ttl_hours=getattr(settings, "app_session_ttl_hours", 168),
        cookie_secure=getattr(settings, "app_cookie_secure", "auto"),
        admin_api_token=settings.admin_api_token,
    )
    application.add_middleware(AppAuthMiddleware, config=auth_config)
    application.include_router(create_auth_router(auth_config))
    application.include_router(monitor_router, prefix="/api/monitor", tags=["Monitor"])
    application.include_router(
        create_short_drama_router(
            repository=_RuntimeAttributeProxy(holder, "repository"),
            pipeline=_RuntimeAttributeProxy(holder, "pipeline"),
            dispatcher=_RuntimeAttributeProxy(holder, "dispatcher"),
            scheduler=_RuntimeAttributeProxy(holder, "scheduler"),
            history_backfill_worker=_RuntimeAttributeProxy(holder, "history_backfill_worker"),
            cookie_manager=_RuntimeAttributeProxy(holder, "cookie_manager"),
            default_check_interval_minutes=settings.check_interval_minutes,
            admin_api_token=settings.admin_api_token,
        )
    )

    @application.get("/", include_in_schema=False)
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/following")

    return application


app = create_app()


def _default_runtime() -> ShortDramaRuntime:
    if SHORT_DRAMA_RUNTIME is not None:
        return SHORT_DRAMA_RUNTIME
    return _DEFAULT_RUNTIME_HOLDER.get()


async def startup_monitor(runtime: ShortDramaRuntime | None = None) -> None:
    active_runtime = runtime or _default_runtime()
    if active_runtime.settings.legacy_monitor_enabled:
        await monitor_service.auto_resume()
    await active_runtime.start()


async def shutdown_monitor(runtime: ShortDramaRuntime | None = None) -> None:
    active_runtime = runtime or _default_runtime()
    await active_runtime.shutdown()
    if active_runtime.settings.legacy_monitor_enabled:
        await monitor_service.shutdown()
