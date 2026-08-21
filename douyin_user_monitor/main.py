from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from douyin_user_monitor.api.monitor import monitor_service, router as monitor_router
from douyin_user_monitor.short_drama_runtime import build_short_drama_runtime
from douyin_user_monitor.web.short_drama import create_short_drama_router

SHORT_DRAMA_RUNTIME = build_short_drama_runtime()

app = FastAPI(title="AI 短剧追更系统", version="1.0.0")
app.include_router(monitor_router, prefix="/api/monitor", tags=["Monitor"])
app.include_router(
    create_short_drama_router(
        repository=SHORT_DRAMA_RUNTIME.repository,
        pipeline=SHORT_DRAMA_RUNTIME.pipeline,
        dispatcher=SHORT_DRAMA_RUNTIME.dispatcher,
        scheduler=SHORT_DRAMA_RUNTIME.scheduler,
        history_backfill_worker=SHORT_DRAMA_RUNTIME.history_backfill_worker,
        cookie_manager=SHORT_DRAMA_RUNTIME.cookie_manager,
        default_check_interval_minutes=SHORT_DRAMA_RUNTIME.settings.check_interval_minutes,
        admin_api_token=SHORT_DRAMA_RUNTIME.settings.admin_api_token,
    )
)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/following")


@app.on_event("startup")
async def startup_monitor() -> None:
    if SHORT_DRAMA_RUNTIME.settings.legacy_monitor_enabled:
        await monitor_service.auto_resume()
    await SHORT_DRAMA_RUNTIME.start()


@app.on_event("shutdown")
async def shutdown_monitor() -> None:
    await SHORT_DRAMA_RUNTIME.shutdown()
    if SHORT_DRAMA_RUNTIME.settings.legacy_monitor_enabled:
        await monitor_service.shutdown()
