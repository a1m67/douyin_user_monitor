from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from douyin_user_monitor.api.monitor import monitor_service, router as monitor_router
from douyin_user_monitor.api.ig_monitor import ig_service, router as ig_router

app = FastAPI(title="Douyin User Monitor", version="0.3.0")
app.include_router(monitor_router, prefix="/api/monitor", tags=["Monitor"])
app.include_router(ig_router, prefix="/api/instagram", tags=["Instagram"])


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/api/monitor/dashboard")


@app.on_event("startup")
async def startup_monitor() -> None:
    await monitor_service.auto_resume()
    await ig_service.auto_resume()


@app.on_event("shutdown")
async def shutdown_monitor() -> None:
    await ig_service.shutdown()
    await monitor_service.shutdown()
