from __future__ import annotations

from urllib.parse import urlparse

import httpx
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from douyin_user_monitor.api.models import ErrorResponseModel, ResponseModel
from douyin_user_monitor.monitor.cookie_liveness import CookieLivenessConfig, CookieLivenessService
from douyin_user_monitor.monitor.hermes_weixin_sender import HermesWeixinConfig, HermesWeixinSender
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.service import (
    MIN_INTERVAL_HOURS,
    MODE_COVERAGE,
    MODE_INTERVAL,
    MonitorService,
)
from douyin_user_monitor.monitor.telegram_notifier import TelegramNotifier
from douyin_user_monitor.monitor.user_lookup import find_user_by_id
from douyin_user_monitor.monitor.storage import MonitorStorage
from douyin_user_monitor.monitor.statistics import build_user_statistics
from douyin_user_monitor.settings import load_settings
from douyin_user_monitor.upstream.douyin_client import DEFAULT_HEADERS, UpstreamDouyinClient

router = APIRouter()
AVATAR_CACHE_SECONDS = 3600
HTML_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}

SETTINGS = load_settings()
DASHBOARD_PATH = SETTINGS.project_root / "douyin_user_monitor" / "web" / "dashboard.html"
STATISTICS_DASHBOARD_PATH = SETTINGS.project_root / "douyin_user_monitor" / "web" / "statistics.html"

UPSTREAM_CLIENT = UpstreamDouyinClient(SETTINGS.upstream.base_url, SETTINGS.upstream.timeout_seconds)
MONITOR_STORAGE = MonitorStorage(SETTINGS.monitor.state_path)
TG_SETTINGS = SETTINGS.notifications.telegram
MONITOR_NOTIFIER = (
    TelegramNotifier(
        bot_token=TG_SETTINGS.bot_token,
        chat_id=TG_SETTINGS.chat_id,
        api_base=TG_SETTINGS.api_base,
        timeout_seconds=TG_SETTINGS.timeout_seconds,
    )
    if TG_SETTINGS.enabled
    else NoopMonitorNotifier()
)

HERMES_SETTINGS = SETTINGS.notifications.hermes_weixin
HERMES_SENDER = (
    HermesWeixinSender(
        HermesWeixinConfig(
            enabled=HERMES_SETTINGS.enabled,
            ssh_host=HERMES_SETTINGS.ssh_host,
            ssh_user=HERMES_SETTINGS.ssh_user,
            hermes_home=HERMES_SETTINGS.hermes_home,
            hermes_bin=HERMES_SETTINGS.hermes_bin,
            target=HERMES_SETTINGS.target,
            timeout_seconds=HERMES_SETTINGS.timeout_seconds,
        )
    )
    if HERMES_SETTINGS.enabled
    else None
)

COOKIE_SETTINGS = SETTINGS.cookie_liveness
COOKIE_LIVENESS_SERVICE = CookieLivenessService(
    crawler=UPSTREAM_CLIENT,
    config=CookieLivenessConfig(
        enabled=COOKIE_SETTINGS.enabled,
        interval_hours=COOKIE_SETTINGS.interval_hours,
        stale_days=COOKIE_SETTINGS.stale_days,
        sample_user_count=COOKIE_SETTINGS.sample_user_count,
        min_samples=COOKIE_SETTINGS.min_samples,
        alert_cooldown_hours=COOKIE_SETTINGS.alert_cooldown_hours,
    ),
    alerter=HERMES_SENDER,
)

monitor_service = MonitorService(
    crawler=UPSTREAM_CLIENT,
    storage=MONITOR_STORAGE,
    download_root=SETTINGS.monitor.download_root,
    notifier=MONITOR_NOTIFIER,
    cookie_liveness_service=COOKIE_LIVENESS_SERVICE,
)


class AddUserPayload(BaseModel):
    profile_url: str = Field(min_length=1, description="抖音用户主页链接")


class UpdateUserPayload(BaseModel):
    enabled: bool


class StartMonitorPayload(BaseModel):
    mode: str = Field(default=MODE_INTERVAL, description=f"监控模式: {MODE_INTERVAL} | {MODE_COVERAGE}")
    interval_hours: float = Field(default=0.05, ge=MIN_INTERVAL_HOURS, description="定时模式轮询间隔(小时)")
    coverage_hours: float = Field(default=24.0, gt=0, description="覆盖模式下，全量用户完成一轮的目标小时数")


def _build_error(request: Request, message: str) -> HTTPException:
    detail = ErrorResponseModel(
        code=400,
        message=message,
        router=request.url.path,
        params=dict(request.query_params),
    )
    return HTTPException(status_code=400, detail=detail.model_dump())


def _build_success(request: Request, data: Any) -> ResponseModel:
    return ResponseModel(code=200, router=request.url.path, data=data)


def _find_user_or_raise(user_id: str) -> dict[str, Any]:
    user = find_user_by_id(monitor_service.list_users(), user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _validate_avatar_url(raw_url: Any) -> str:
    avatar_url = str(raw_url or "").strip()
    parsed = urlparse(avatar_url)
    if not avatar_url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=404, detail="用户头像不存在")
    return avatar_url


async def _download_avatar(avatar_url: str) -> httpx.Response:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=SETTINGS.upstream.timeout_seconds,
    ) as client:
        try:
            response = await client.get(avatar_url, headers=DEFAULT_HEADERS)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"头像拉取失败: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"头像拉取失败: status={response.status_code}",
        )
    return response


@router.get("/dashboard", response_class=HTMLResponse, summary="监控管理页面/Monitoring dashboard page")
async def monitor_dashboard() -> str:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail=f"仪表盘文件不存在: {DASHBOARD_PATH}")
    return HTMLResponse(
        content=DASHBOARD_PATH.read_text(encoding="utf-8"),
        headers=HTML_NO_CACHE_HEADERS,
    )


@router.get("/statistics/dashboard", response_class=HTMLResponse, summary="用户统计页面/User statistics page")
async def statistics_dashboard() -> str:
    if not STATISTICS_DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail=f"统计页面文件不存在: {STATISTICS_DASHBOARD_PATH}")
    return HTMLResponse(
        content=STATISTICS_DASHBOARD_PATH.read_text(encoding="utf-8"),
        headers=HTML_NO_CACHE_HEADERS,
    )


@router.get("/users", response_model=ResponseModel, summary="获取监控用户列表/Get monitor users")
async def list_users(request: Request) -> ResponseModel:
    users = await monitor_service.list_users_with_profile()
    return _build_success(request, {"users": users})


@router.get("/statistics", response_model=ResponseModel, summary="获取用户统计/Get user statistics")
async def user_statistics(request: Request) -> ResponseModel:
    users = await monitor_service.list_users_with_profile()
    return _build_success(request, build_user_statistics(users))


@router.get("/users/{user_id}/avatar", summary="代理监控用户头像/Proxy monitor user avatar")
async def proxy_user_avatar(user_id: str) -> Response:
    user = _find_user_or_raise(user_id)
    avatar_url = _validate_avatar_url(user.get("avatar_url"))
    upstream_response = await _download_avatar(avatar_url)
    content_type = upstream_response.headers.get("Content-Type", "image/jpeg")
    return Response(
        content=upstream_response.content,
        media_type=content_type,
        headers={"Cache-Control": f"public, max-age={AVATAR_CACHE_SECONDS}"},
    )


@router.post("/users", response_model=ResponseModel, summary="添加监控用户/Add monitor user")
async def add_user(request: Request, payload: AddUserPayload) -> ResponseModel:
    try:
        result = await monitor_service.add_user_with_status(payload.profile_url)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.api_route(
    "/users/add_by_url",
    methods=["GET", "POST"],
    response_model=ResponseModel,
    summary="通过主页链接快速添加监控用户/Quick add user by profile URL",
)
async def add_user_by_url(
    request: Request,
    profile_url: str = Query(min_length=1, description="抖音用户主页链接"),
) -> ResponseModel:
    try:
        result = await monitor_service.add_user_with_status(profile_url)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.patch("/users/{user_id}", response_model=ResponseModel, summary="更新用户监控状态/Update monitor user")
async def update_user(request: Request, user_id: str, payload: UpdateUserPayload) -> ResponseModel:
    try:
        user = monitor_service.set_user_enabled(user_id, payload.enabled)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.delete("/users/{user_id}", response_model=ResponseModel, summary="删除监控用户/Delete monitor user")
async def delete_user(request: Request, user_id: str) -> ResponseModel:
    try:
        user = monitor_service.remove_user(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.post("/users/{user_id}/run_once", response_model=ResponseModel, summary="手动执行单个用户/Run one user once")
async def run_user_once(request: Request, user_id: str) -> ResponseModel:
    try:
        result = await monitor_service.run_user_once(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post(
    "/users/{user_id}/backfill/start",
    response_model=ResponseModel,
    summary="启动用户历史回填/Start user history backfill",
)
async def start_user_history_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        user = monitor_service.start_user_history_backfill(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.post(
    "/users/{user_id}/backfill/pause",
    response_model=ResponseModel,
    summary="暂停用户历史回填/Pause user history backfill",
)
async def pause_user_history_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        user = monitor_service.pause_user_history_backfill(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.post(
    "/users/{user_id}/backfill/resume",
    response_model=ResponseModel,
    summary="恢复用户历史回填/Resume user history backfill",
)
async def resume_user_history_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        user = monitor_service.resume_user_history_backfill(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.post(
    "/users/{user_id}/backfill/run_once",
    response_model=ResponseModel,
    summary="手动执行一页历史回填/Run one history backfill page",
)
async def run_user_history_backfill_once(request: Request, user_id: str) -> ResponseModel:
    try:
        result = await monitor_service.run_user_history_backfill_once(user_id)
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.get("/status", response_model=ResponseModel, summary="获取监控状态/Get monitor status")
async def monitor_status(request: Request) -> ResponseModel:
    return _build_success(request, monitor_service.get_status())


@router.post("/start", response_model=ResponseModel, summary="启动监控/Start monitoring")
async def start_monitor(request: Request, payload: StartMonitorPayload) -> ResponseModel:
    try:
        status = await monitor_service.start_monitoring(
            mode=payload.mode,
            interval_hours=payload.interval_hours,
            coverage_hours=payload.coverage_hours,
        )
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, status)


@router.post("/stop", response_model=ResponseModel, summary="停止监控/Stop monitoring")
async def stop_monitor(request: Request) -> ResponseModel:
    try:
        status = await monitor_service.stop_monitoring()
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, status)


@router.post("/run_once", response_model=ResponseModel, summary="手动执行一次监控/Run monitor once")
async def run_once(request: Request) -> ResponseModel:
    try:
        result = await monitor_service.run_once()
    except Exception as exc:  # noqa: BLE001
        raise _build_error(request, str(exc))
    return _build_success(request, result)
