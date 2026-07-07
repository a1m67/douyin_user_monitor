from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from douyin_user_monitor.api.models import ErrorResponseModel, ResponseModel
from douyin_user_monitor.monitor.ig_crawler import IgCrawler
from douyin_user_monitor.monitor.ig_downloader import IgDownloader
from douyin_user_monitor.monitor.ig_service import IgService
from douyin_user_monitor.monitor.ig_storage import IgStorage
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.statistics import build_user_statistics
from douyin_user_monitor.monitor.telegram_notifier import TelegramNotifier
from douyin_user_monitor.settings import load_settings

router = APIRouter()
AVATAR_CACHE_SECONDS = 3600
HTML_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}

SETTINGS = load_settings()
IG_SETTINGS = SETTINGS.instagram
STATISTICS_DASHBOARD_PATH = SETTINGS.project_root / "douyin_user_monitor" / "web" / "statistics.html"

IG_CRAWLER = IgCrawler(
    login_user=IG_SETTINGS.login_user,
    session_file=IG_SETTINGS.session_file,
    request_delay=IG_SETTINGS.request_delay_seconds,
    proxy=IG_SETTINGS.proxy if hasattr(IG_SETTINGS, 'proxy') else None,
)

IG_STORAGE = IgStorage(IG_SETTINGS.state_path)
IG_DOWNLOADER = IgDownloader(IG_SETTINGS.download_root)

TG_SETTINGS = SETTINGS.notifications.telegram
IG_NOTIFIER = (
    TelegramNotifier(
        bot_token=TG_SETTINGS.bot_token,
        chat_id=TG_SETTINGS.chat_id,
        api_base=TG_SETTINGS.api_base,
        timeout_seconds=TG_SETTINGS.timeout_seconds,
    )
    if TG_SETTINGS.enabled
    else NoopMonitorNotifier()
)

ig_service = IgService(
    crawler=IG_CRAWLER,
    downloader=IG_DOWNLOADER,
    storage=IG_STORAGE,
    notifier=IG_NOTIFIER,
    request_delay=IG_SETTINGS.request_delay_seconds,
)


class AddIgUserPayload(BaseModel):
    username: str = Field(min_length=1, description="Instagram 用户名")


class UpdateIgUserPayload(BaseModel):
    enabled: bool


class StartIgMonitorPayload(BaseModel):
    interval_hours: float = Field(default=6.0, gt=0, description="轮询间隔(小时)")


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


@router.get("/users", response_model=ResponseModel, summary="获取 Instagram 监控用户列表")
async def list_users(request: Request) -> ResponseModel:
    return _build_success(request, {"users": ig_service.get_users()})


@router.get("/statistics/dashboard", response_class=HTMLResponse, summary="Instagram 用户统计页面")
async def statistics_dashboard() -> str:
    if not STATISTICS_DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail=f"统计页面文件不存在: {STATISTICS_DASHBOARD_PATH}")
    return HTMLResponse(
        content=STATISTICS_DASHBOARD_PATH.read_text(encoding="utf-8"),
        headers=HTML_NO_CACHE_HEADERS,
    )


@router.get("/statistics", response_model=ResponseModel, summary="获取 Instagram 用户统计")
async def get_statistics(request: Request) -> ResponseModel:
    users = ig_service.get_users()
    # 转换 Instagram 数据格式以兼容 build_user_statistics
    converted_users = []
    for user in users:
        converted = {
            "id": user.get("id"),
            "nickname": user.get("username") or user.get("full_name") or "-",
            "sec_user_id": user.get("username", ""),
            "profile_url": None,
            "avatar_url": user.get("avatar_url"),
            "enabled": user.get("enabled", True),
            "account_status": "normal",
            "account_status_label": "正常",
            "downloaded_count": user.get("downloaded_count", 0),
            "downloaded_aweme_ids": user.get("downloaded_post_ids", []),
            "download_records": [],
            "last_checked_at": user.get("last_checked_at"),
            "last_download_at": user.get("last_download_at"),
            "last_error": user.get("last_error"),
        }
        # 转换 download_records 格式
        for record in user.get("download_records", []):
            posted_at = record.get("posted_at")
            # 将 Unix 时间戳转换为 ISO 格式
            if isinstance(posted_at, (int, float)) and posted_at > 0:
                from datetime import datetime, timezone, timedelta
                dt = datetime.fromtimestamp(posted_at, tz=timezone(timedelta(hours=8)))
                posted_at_str = dt.isoformat(timespec="seconds")
            else:
                posted_at_str = str(posted_at) if posted_at else None

            converted_record = {
                "media_type": record.get("media_type", "video"),
                "image_count": 1 if record.get("media_type") == "image" else 0,
                "total_size_bytes": record.get("total_size_bytes", 0),
                "publish_time": posted_at_str,
                "downloaded_at": record.get("downloaded_at"),
            }
            converted["download_records"].append(converted_record)
        converted_users.append(converted)
    return _build_success(request, build_user_statistics(converted_users))


@router.post("/users", response_model=ResponseModel, summary="添加 Instagram 监控用户")
async def add_user(request: Request, payload: AddIgUserPayload) -> ResponseModel:
    try:
        user = await ig_service.add_user(payload.username)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.patch("/users/{user_id}", response_model=ResponseModel, summary="更新 Instagram 用户状态")
async def update_user(request: Request, user_id: str, payload: UpdateIgUserPayload) -> ResponseModel:
    try:
        user = ig_service.update_user_enabled(user_id, payload.enabled)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, user)


@router.delete("/users/{user_id}", response_model=ResponseModel, summary="删除 Instagram 监控用户")
async def delete_user(request: Request, user_id: str) -> ResponseModel:
    try:
        ig_service.remove_user(user_id)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, {"deleted": user_id})


@router.post("/users/{user_id}/run_once", response_model=ResponseModel, summary="手动同步单个 Instagram 用户")
async def run_once_user(request: Request, user_id: str) -> ResponseModel:
    try:
        user = ig_service.get_user(user_id)
        summary = {"checked_users": 0, "downloaded_items": 0, "errors": []}
        await ig_service.sync_one_user(user, summary)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, summary)


@router.post("/start", response_model=ResponseModel, summary="启动 Instagram 监控")
async def start_monitor(request: Request, payload: StartIgMonitorPayload) -> ResponseModel:
    try:
        result = await ig_service.start_monitoring(payload.interval_hours)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post("/stop", response_model=ResponseModel, summary="停止 Instagram 监控")
async def stop_monitor(request: Request) -> ResponseModel:
    try:
        result = await ig_service.stop_monitoring()
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post("/run_once", response_model=ResponseModel, summary="手动执行一次全部 Instagram 同步")
async def run_once_all(request: Request) -> ResponseModel:
    try:
        result = await ig_service.run_once()
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.get("/status", response_model=ResponseModel, summary="获取 Instagram 监控状态")
async def monitor_status(request: Request) -> ResponseModel:
    return _build_success(request, ig_service.get_status())


@router.post("/users/{user_id}/backfill/start", response_model=ResponseModel, summary="启动历史回填")
async def start_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        result = ig_service.start_history_backfill(user_id)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post("/users/{user_id}/backfill/pause", response_model=ResponseModel, summary="暂停历史回填")
async def pause_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        result = ig_service.pause_history_backfill(user_id)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post("/users/{user_id}/backfill/resume", response_model=ResponseModel, summary="恢复历史回填")
async def resume_backfill(request: Request, user_id: str) -> ResponseModel:
    try:
        result = ig_service.resume_history_backfill(user_id)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.post("/users/{user_id}/backfill/run_once", response_model=ResponseModel, summary="手动执行一页历史回填")
async def run_backfill_once(request: Request, user_id: str) -> ResponseModel:
    try:
        result = await ig_service.run_history_backfill_once(user_id)
    except Exception as exc:
        raise _build_error(request, str(exc))
    return _build_success(request, result)


@router.get("/users/{user_id}/avatar", summary="代理 Instagram 用户头像")
async def proxy_avatar(user_id: str) -> Response:
    try:
        user = ig_service.get_user(user_id)
    except Exception:
        raise HTTPException(status_code=404, detail="用户不存在")
    avatar_url = (user.get("avatar_url") or "").strip()
    if not avatar_url:
        raise HTTPException(status_code=404, detail="用户头像不存在")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(avatar_url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"头像拉取失败: {exc}")
    content_type = resp.headers.get("Content-Type", "image/jpeg")
    return Response(
        content=resp.content,
        media_type=content_type,
        headers={"Cache-Control": f"max-age={AVATAR_CACHE_SECONDS}"},
    )
