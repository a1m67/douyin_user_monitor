"""FastAPI routes for the server-rendered short-drama dashboard."""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.maintenance import backup_database, doctor_database
from douyin_user_monitor.services.episode_pipeline import (
    HistoryBackfillResult,
    ShortDramaPipeline,
    SyncResult,
)


class SchedulerStatus(Protocol):
    def health_status(self) -> str:
        ...

    async def run_account_once(self, account_id: str, *, force: bool = False) -> Any:
        ...

    def crawler_status(self) -> dict[str, object]:
        ...


class HistoryBackfillWorkerControl(Protocol):
    def wake(self) -> None:
        ...

    def health_status(self) -> str:
        ...


class CookieManagerControl(Protocol):
    def status(self) -> dict[str, Any]: ...
    def save(self, value: object) -> dict[str, Any]: ...
    async def test(self) -> dict[str, Any]: ...


class AddAccountPayload(BaseModel):
    homepage_url: str = Field(min_length=1)
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class UpdateAccountPayload(BaseModel):
    nickname: str | None = Field(default=None, min_length=1)
    homepage_url: str | None = None
    enabled: bool | None = None
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class UpdateShowPayload(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    aliases: list[str] | None = None
    status: str | None = None
    expected_episode_count: int | None = Field(default=None, ge=1, le=100000)


class UpdateShowSeasonPayload(BaseModel):
    expected_episode_count: int | None = Field(default=None, ge=1, le=100000)
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class WatchProgressPayload(BaseModel):
    watched_episode_number: int = Field(ge=0, le=100000)


class IgnoreShowPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class MergeShowPayload(BaseModel):
    source_show_id: int = Field(gt=0)


class ReviewPayload(BaseModel):
    show_id: int | None = None
    new_show_title: str | None = Field(default=None, max_length=120)
    episode_number: int = Field(ge=0, le=100000)
    season_number: int = Field(default=1, ge=1, le=1000)
    learn_alias: bool = False


class BatchIgnoreReviewPayload(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=500)


class MoveEpisodePayload(BaseModel):
    target_show_id: int = Field(gt=0)
    season_number: int = Field(ge=1, le=1000)
    episode_number: int = Field(ge=0, le=100000)


class MoveEpisodeSourcePayload(BaseModel):
    target_show_id: int = Field(gt=0)
    season_number: int = Field(ge=1, le=1000)
    episode_number: int = Field(ge=0, le=100000)


class BatchEpisodeSeasonPayload(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=500)
    season_number: int = Field(ge=1, le=1000)


class BatchVideoPayload(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=500)


class ReparseAccountPayload(BaseModel):
    scope: str = Field(
        default="legacy_ignored",
        pattern="^(legacy_ignored|ignored|ignored_review)$",
    )


class CookieUpdatePayload(BaseModel):
    cookie: Any


def create_short_drama_router(
    *,
    repository: ShortDramaRepository,
    pipeline: ShortDramaPipeline,
    dispatcher: NotificationDispatcher | None = None,
    scheduler: SchedulerStatus | None = None,
    history_backfill_worker: HistoryBackfillWorkerControl | None = None,
    cookie_manager: CookieManagerControl | None = None,
    page_path: Path | None = None,
    default_check_interval_minutes: int = 10,
    admin_api_token: str = "",
) -> APIRouter:
    router = APIRouter()
    html_path = page_path or Path(__file__).with_name("short_drama.html")
    asset_dir = html_path.parent

    def page() -> HTMLResponse:
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="短剧 Dashboard 文件不存在")
        return HTMLResponse(
            html_path.read_text(encoding="utf-8").replace(
                "{{DEFAULT_CHECK_INTERVAL_MINUTES}}", str(default_check_interval_minutes)
            ),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @router.get("/shows", response_class=HTMLResponse, include_in_schema=False)
    async def shows_page() -> HTMLResponse:
        return page()

    @router.get("/shows/{show_id}", response_class=HTMLResponse, include_in_schema=False)
    async def show_detail_page(show_id: int) -> HTMLResponse:
        _ = show_id
        return page()

    @router.get("/manifest.webmanifest", include_in_schema=False)
    async def pwa_manifest() -> Response:
        return Response((asset_dir / "manifest.webmanifest").read_text(encoding="utf-8"), media_type="application/manifest+json")

    @router.get("/sw.js", include_in_schema=False)
    async def service_worker() -> Response:
        return Response((asset_dir / "sw.js").read_text(encoding="utf-8"), media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    @router.get("/pwa-icon.svg", include_in_schema=False)
    async def pwa_icon() -> Response:
        return Response((asset_dir / "pwa-icon.svg").read_text(encoding="utf-8"), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})

    @router.get("/following", response_class=HTMLResponse, include_in_schema=False)
    async def following_page() -> HTMLResponse:
        return page()

    @router.get("/updates", response_class=HTMLResponse, include_in_schema=False)
    async def updates_page() -> HTMLResponse:
        return page()

    @router.get("/accounts", response_class=HTMLResponse, include_in_schema=False)
    async def accounts_page() -> HTMLResponse:
        return page()

    @router.get("/videos", response_class=HTMLResponse, include_in_schema=False)
    async def videos_page() -> HTMLResponse:
        return page()

    @router.get("/review", response_class=HTMLResponse, include_in_schema=False)
    async def review_page() -> HTMLResponse:
        return page()

    @router.get("/status", response_class=HTMLResponse, include_in_schema=False)
    async def status_page() -> HTMLResponse:
        return page()

    @router.get("/settings/crawler", response_class=HTMLResponse, include_in_schema=False)
    async def crawler_settings_page() -> HTMLResponse:
        return page()

    @router.get("/diagnostics", response_class=HTMLResponse, include_in_schema=False)
    async def diagnostics_page() -> HTMLResponse:
        return page()

    @router.get("/quality", response_class=HTMLResponse, include_in_schema=False)
    async def quality_page() -> HTMLResponse:
        return page()

    def health_payload() -> dict[str, str]:
        repository.counts()
        return {
            "status": "ok",
            "database": "ok",
            "scheduler": scheduler.health_status() if scheduler is not None else "not_started",
            "history_backfill_worker": (
                history_backfill_worker.health_status()
                if history_backfill_worker is not None
                else "not_started"
            ),
        }

    @router.get("/health")
    async def root_health() -> dict[str, str]:
        return health_payload()

    configured_admin_token = admin_api_token.strip()

    async def require_admin_token(request: Request) -> None:
        if not configured_admin_token or request.method in {"GET", "HEAD", "OPTIONS"}:
            return
        authorization = request.headers.get("authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        if (
            not separator
            or scheme.casefold() != "bearer"
            or not secrets.compare_digest(credential, configured_admin_token)
        ):
            raise HTTPException(status_code=401, detail="管理 API 需要有效的 Bearer token")

    api = APIRouter(
        prefix="/api/short-drama",
        tags=["Short drama"],
        dependencies=[Depends(require_admin_token)],
    )
    @api.get("/shows")
    async def list_shows(
        account_id: str | None = None,
        include_ignored: bool = False,
        ignored: str | None = Query(default=None, pattern="^(normal|ignored|all)$"),
        include_empty: bool = False,
        following: bool | None = None,
        q: str | None = Query(default=None, max_length=120),
        sort: str = Query(default="recent", pattern="^(recent|title|episode_count|latest_episode)$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        ignored_filter = ignored or ("all" if include_ignored else "normal")
        return {
            "shows": repository.list_show_summaries(
                account_id=account_id,
                ignored=ignored_filter,
                following=following,
                include_empty=include_empty,
                q=q,
                sort=sort,
                limit=limit,
            )
        }

    @api.get("/shows/{show_id}")
    async def get_show(show_id: int) -> dict[str, Any]:
        show = repository.get_show_detail(show_id)
        if show is None:
            raise HTTPException(status_code=404, detail="短剧不存在")
        return {"show": show}

    @api.patch("/shows/{show_id}")
    async def update_show(show_id: int, payload: UpdateShowPayload) -> dict[str, Any]:
        try:
            show = repository.update_show(show_id, **payload.model_dump(exclude_unset=True))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"show": show}

    @api.post("/shows/{target_show_id}/merge")
    async def merge_show(target_show_id: int, payload: MergeShowPayload) -> dict[str, Any]:
        try:
            show = repository.merge_show(payload.source_show_id, target_show_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"show": show}

    @api.get("/shows/{show_id}/seasons")
    async def list_show_seasons(show_id: int) -> dict[str, Any]:
        if repository.get_show(show_id) is None:
            raise HTTPException(status_code=404, detail="短剧不存在")
        return {"seasons": repository.list_show_seasons(show_id)}

    @api.patch("/shows/{show_id}/seasons/{season_number}")
    async def update_show_season(
        show_id: int, season_number: int, payload: UpdateShowSeasonPayload
    ) -> dict[str, Any]:
        try:
            season = repository.update_show_season(
                show_id, season_number, **payload.model_dump(exclude_unset=True)
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"season": season, "show": repository.get_show_detail(show_id)}

    @api.get("/shows/{show_id}/seasons/{season_number}/watch-progress")
    async def get_watch_progress(show_id: int, season_number: int) -> dict[str, Any]:
        if repository.get_show(show_id) is None:
            raise HTTPException(status_code=404, detail="短剧不存在")
        try:
            return {"progress": repository.get_watch_progress(show_id, season_number)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.put("/shows/{show_id}/seasons/{season_number}/watch-progress")
    async def set_watch_progress(
        show_id: int, season_number: int, payload: WatchProgressPayload
    ) -> dict[str, Any]:
        try:
            progress = repository.set_watch_progress(
                show_id, season_number, payload.watched_episode_number
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"progress": progress, "show": repository.get_show_detail(show_id)}

    @api.get("/updates")
    async def list_updates(
        following_only: bool = True,
        unread_only: bool = False,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        result = repository.list_update_events(
            following_only=following_only,
            unread_only=unread_only,
            page=page,
            page_size=page_size,
        )
        result["unread_count"] = repository.unread_update_count()
        result["following_unread_count"] = repository.unread_update_count(
            following_only=True
        )
        result["groups"] = _group_update_events(result["events"])
        return result

    @api.post("/updates/read-all")
    async def mark_updates_read(show_id: int | None = None) -> dict[str, int]:
        return {"marked_read": repository.mark_updates_read(show_id=show_id)}

    @api.post("/updates/{event_id}/read")
    async def mark_update_read(event_id: int) -> dict[str, Any]:
        try:
            return {"event": repository.mark_update_read(event_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/shows/{show_id}/ignore")
    async def ignore_show(
        show_id: int, payload: IgnoreShowPayload | None = None
    ) -> dict[str, Any]:
        try:
            show = pipeline.ignore_show(show_id, reason=payload.reason if payload else None)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"show": show}

    @api.post("/shows/{show_id}/restore")
    async def restore_show(show_id: int) -> dict[str, Any]:
        try:
            show = pipeline.restore_show(show_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"show": show}

    @api.post("/shows/{show_id}/follow")
    async def follow_show(show_id: int) -> dict[str, Any]:
        try:
            return {"show": repository.set_show_following(show_id, following=True)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/shows/{show_id}/unfollow")
    async def unfollow_show(show_id: int) -> dict[str, Any]:
        try:
            return {"show": repository.set_show_following(show_id, following=False)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.delete("/shows/{show_id}/episodes/{episode_id}")
    async def remove_episode(show_id: int, episode_id: int) -> dict[str, Any]:
        try:
            return {"result": pipeline.remove_episode(show_id, episode_id)}
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/episodes/{episode_id}/move")
    async def move_episode(episode_id: int, payload: MoveEpisodePayload) -> dict[str, Any]:
        try:
            return {"result": repository.move_episode(episode_id, **payload.model_dump())}
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/episode-sources/{source_id}/move")
    async def move_episode_source(source_id: int, payload: MoveEpisodeSourcePayload) -> dict[str, Any]:
        try:
            return {"result": repository.move_episode_source(source_id, **payload.model_dump())}
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/episodes/batch-season")
    async def batch_episode_season(payload: BatchEpisodeSeasonPayload) -> dict[str, int]:
        try:
            return {"updated_count": repository.batch_update_episode_season(payload.episode_ids, payload.season_number)}
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/corrections")
    async def list_corrections(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        return {"corrections": repository.list_manual_corrections(limit)}

    @api.delete("/shows/{show_id}/episodes/{episode_id}/sources/{source_id}")
    async def remove_episode_source(
        show_id: int, episode_id: int, source_id: int
    ) -> dict[str, Any]:
        try:
            return {
                "result": pipeline.remove_episode_source(show_id, episode_id, source_id)
            }
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/repair-consistency")
    async def repair_consistency() -> dict[str, Any]:
        return {"result": repository.repair_episode_and_show_consistency()}

    @api.get("/accounts")
    async def list_accounts() -> dict[str, Any]:
        return {"accounts": repository.list_accounts()}

    @api.get("/settings/crawler")
    async def crawler_settings() -> dict[str, Any]:
        empty = {"configured": False, "status": "not_configured", "last_validated_at": None, "last_updated_at": None}
        return {"cookie": cookie_manager.status() if cookie_manager else empty}

    @api.get("/diagnostics")
    async def diagnostics() -> dict[str, Any]:
        path = repository.database_path
        doctor = doctor_database(path)
        return {"database": {"path": path.name, "size_bytes": path.stat().st_size,
                "schema_version": repository.schema_version(), "doctor_ok": doctor.ok,
                "backup_count": len(list((path.parent / "backups").glob("app-*.db")))},
                "scheduler": scheduler.health_status() if scheduler else "not_started",
                "crawler": scheduler.crawler_status() if scheduler and hasattr(scheduler,"crawler_status") else {},
                "cookie": cookie_manager.status() if cookie_manager else {"status":"not_configured"},
                "features": {"llm": "configured_or_disabled", "ocr": "configured_or_disabled"},
                "parser_metrics_24h": repository.system_status()["scan_runs_24h"]}

    @api.get("/quality")
    async def data_quality(stale_days: int = Query(default=30, ge=1, le=3650)) -> dict[str, Any]:
        return repository.data_quality_report(stale_days=stale_days)

    @api.post("/diagnostics/doctor")
    async def run_doctor() -> dict[str, Any]:
        report = doctor_database(repository.database_path)
        return {"ok": report.ok, "checks": report.checks}

    @api.post("/diagnostics/backup")
    async def create_backup() -> dict[str, Any]:
        path = backup_database(repository.database_path)
        return {"created": True, "filename": path.name}

    @api.put("/settings/crawler/cookie")
    async def update_crawler_cookie(payload: CookieUpdatePayload) -> dict[str, Any]:
        if cookie_manager is None:
            raise HTTPException(status_code=503, detail="Cookie 管理未启用")
        try:
            return {"cookie": cookie_manager.save(payload.cookie)}
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/settings/crawler/test")
    async def test_crawler_cookie() -> dict[str, Any]:
        if cookie_manager is None:
            raise HTTPException(status_code=503, detail="Cookie 管理未启用")
        return {"result": await cookie_manager.test(), "cookie": cookie_manager.status()}

    @api.post("/accounts", status_code=201)
    async def add_account(payload: AddAccountPayload) -> dict[str, Any]:
        try:
            account, created = await pipeline.add_account(
                payload.homepage_url,
                check_interval_minutes=payload.check_interval_minutes,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"account": account, "created": created}

    @api.patch("/accounts/{account_id}")
    async def update_account(account_id: str, payload: UpdateAccountPayload) -> dict[str, Any]:
        try:
            account = repository.update_account(account_id, **payload.model_dump(exclude_unset=True))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"account": account}

    @api.delete("/accounts/{account_id}")
    async def delete_account(account_id: str) -> dict[str, Any]:
        try:
            account = repository.delete_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"account": account}

    @api.post("/accounts/{account_id}/run-once")
    async def run_account_once(account_id: str, force: bool = False) -> dict[str, Any]:
        try:
            if scheduler is not None:
                check = await scheduler.run_account_once(account_id, force=force)
                if not check.success:
                    if getattr(check, "circuit_open", False):
                        raise HTTPException(status_code=503, detail=check.error)
                    raise RuntimeError(check.error or "账号检查失败")
                result = check.sync_result
                if result is None:
                    raise RuntimeError("账号检查没有返回同步结果")
            else:
                result = await pipeline.sync_account(account_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": _sync_result(result)}

    @api.post("/accounts/{account_id}/history/start")
    async def start_history_backfill(account_id: str) -> dict[str, Any]:
        try:
            account = pipeline.start_history_backfill(account_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if history_backfill_worker is not None:
            history_backfill_worker.wake()
        return {"account": account}

    @api.post("/accounts/{account_id}/history/pause")
    async def pause_history_backfill(account_id: str) -> dict[str, Any]:
        try:
            account = pipeline.pause_history_backfill(account_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"account": account}

    @api.post("/accounts/{account_id}/history/resume")
    async def resume_history_backfill(account_id: str) -> dict[str, Any]:
        try:
            account = pipeline.resume_history_backfill(account_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if history_backfill_worker is not None:
            history_backfill_worker.wake()
        return {"account": account}

    @api.post("/accounts/{account_id}/history/next-page")
    async def run_history_backfill_page(account_id: str) -> dict[str, Any]:
        try:
            result = await pipeline.run_history_backfill_page(account_id)
        except (ValueError, KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": _history_backfill_result(result)}

    @api.post("/accounts/{account_id}/reparse")
    async def reparse_account(
        account_id: str,
        payload: ReparseAccountPayload | None = None,
    ) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                pipeline.reparse_account,
                account_id,
                scope=payload.scope if payload is not None else "legacy_ignored",
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": _reparse_result(result)}

    @api.get("/videos")
    async def list_videos(
        needs_review: bool | None = None,
        classification_status: str | None = Query(default=None, pattern="^(matched|ignored|review)$"),
        account_id: str | None = None, show_id: int | None = None,
        parser_method: str | None = None,
        content_type: str | None = Query(default=None, pattern="^(episode|trailer|show_content|unknown|non_drama)$"),
        q: str | None = Query(default=None, max_length=200), date_from: str | None = None, date_to: str | None = None,
        page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200),
        limit: int | None = Query(default=None, ge=1, le=500),
    ) -> dict[str, Any]:
        return repository.search_videos(needs_review=needs_review, classification_status=classification_status, account_id=account_id, show_id=show_id, parser_method=parser_method, content_type=content_type, q=q, date_from=date_from, date_to=date_to, page=page, page_size=min(limit or page_size, 200))

    @api.post("/videos/{video_id}/reparse")
    async def reparse_video(video_id: int) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(pipeline.reparse_video, video_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "video": result.video,
            "status": result.status,
            "new_episode": result.new_episode,
        }

    @api.post("/videos/batch-ignore")
    async def batch_ignore_videos(payload: BatchVideoPayload) -> dict[str, int]:
        return {"ignored_count": pipeline.ignore_videos(payload.video_ids)}

    @api.post("/videos/batch-reparse")
    async def batch_reparse_videos(payload: BatchVideoPayload) -> dict[str, Any]:
        try:
            results = await asyncio.to_thread(pipeline.reparse_videos, payload.video_ids)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"reparsed_count": len(results), "results": [
            {"video": item.video, "status": item.status, "new_episode": item.new_episode}
            for item in results
        ]}

    @api.post("/reviews/batch-ignore")
    async def batch_ignore_reviews(payload: BatchIgnoreReviewPayload) -> dict[str, Any]:
        ignored_count = pipeline.ignore_reviews(payload.video_ids)
        return {"ignored_count": ignored_count}

    @api.post("/reviews/{video_id}/ignore")
    async def ignore_review(video_id: int) -> dict[str, Any]:
        try:
            video = pipeline.ignore_review(video_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"video": video}

    @api.post("/reviews/{video_id}")
    async def confirm_review(video_id: int, payload: ReviewPayload) -> dict[str, Any]:
        try:
            result = pipeline.confirm_review(
                video_id,
                show_id=payload.show_id,
                new_show_title=payload.new_show_title,
                episode_number=payload.episode_number,
                season_number=payload.season_number,
                learn_alias=payload.learn_alias,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.update is not None and dispatcher is not None:
            await dispatcher.dispatch(result.update)
        return {
            "video": result.video,
            "show": result.show,
            "episode": result.episode,
            "new_episode": result.update is not None,
        }

    @api.get("/status")
    async def status() -> dict[str, Any]:
        result = repository.system_status()
        result["scheduler"] = scheduler.health_status() if scheduler is not None else "not_started"
        result["crawler"] = (
            scheduler.crawler_status()
            if scheduler is not None and hasattr(scheduler, "crawler_status")
            else {"enabled": False, "state": "closed", "reason": None, "retry_at": None}
        )
        result["history_backfill_worker"] = (
            history_backfill_worker.health_status()
            if history_backfill_worker is not None
            else "not_started"
        )
        result["enabled_notification_channels"] = list(dispatcher.enabled_channels) if dispatcher else []
        return result

    @api.get("/health")
    async def health() -> dict[str, str]:
        return health_payload()

    router.include_router(api)
    return router


def _group_update_events(events: list[dict[str, Any]], *, window_hours: int = 24) -> list[dict[str, Any]]:
    """Group adjacent events for presentation only; database events stay intact."""
    groups: list[dict[str, Any]] = []
    for event in events:
        occurred = str(event.get("occurred_at") or "")
        try:
            timestamp = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except ValueError:
            timestamp = None
        key = (int(event["show_id"]), int(event.get("season_number") or 1))
        target = groups[-1] if groups and groups[-1]["key"] == key else None
        if target is not None and timestamp is not None and target.get("_timestamp") is not None:
            if abs((target["_timestamp"] - timestamp).total_seconds()) > window_hours * 3600:
                target = None
        if target is None:
            target = {
                "key": key,
                "show_id": event["show_id"],
                "show_title": event["show_title"],
                "season_number": event.get("season_number", 1),
                "episode_numbers": [],
                "events": [],
                "_timestamp": timestamp,
            }
            groups.append(target)
        target["episode_numbers"].append(int(event["episode_number"]))
        target["events"].append(event)
    for group in groups:
        group.pop("key", None)
        group.pop("_timestamp", None)
        numbers = group["episode_numbers"]
        group["episode_start"] = min(numbers)
        group["episode_end"] = max(numbers)
        group["count"] = len(numbers)
    return groups


def _sync_result(result: SyncResult) -> dict[str, Any]:
    return {
        "account_id": result.account["id"],
        "initial_sync": result.initial_sync,
        "fetched_videos": result.fetched_videos,
        "new_videos": result.new_videos,
        "duplicate_videos": result.duplicate_videos,
        "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos,
        "new_episode_count": len(result.new_episode_updates),
    }


def _history_backfill_result(result: HistoryBackfillResult) -> dict[str, Any]:
    return {
        "account_id": result.account["id"],
        "history_sync": result.account["history_sync"],
        "fetched_videos": result.fetched_videos,
        "new_videos": result.new_videos,
        "duplicate_videos": result.duplicate_videos,
        "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos,
    }


def _reparse_result(result: Any) -> dict[str, Any]:
    return {
        "account_id": result.account["id"],
        "requested_videos": result.requested_videos,
        "matched_videos": result.matched_videos,
        "review_videos": result.review_videos,
        "ignored_videos": result.ignored_videos,
        "new_episode_count": result.new_episode_count,
    }
