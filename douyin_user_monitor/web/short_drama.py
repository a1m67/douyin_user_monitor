"""FastAPI routes for the server-rendered short-drama dashboard."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import (
    HistoryBackfillResult,
    ShortDramaPipeline,
    SyncResult,
)


class SchedulerStatus(Protocol):
    def health_status(self) -> str:
        ...

    async def run_account_once(self, account_id: str) -> Any:
        ...


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


class MergeShowPayload(BaseModel):
    source_show_id: int = Field(gt=0)


class ReviewPayload(BaseModel):
    show_id: int | None = None
    new_show_title: str | None = Field(default=None, max_length=120)
    episode_number: int = Field(ge=1, le=100000)


class BatchIgnoreReviewPayload(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=500)


class ReparseAccountPayload(BaseModel):
    scope: str = Field(
        default="legacy_ignored",
        pattern="^(legacy_ignored|ignored|ignored_review)$",
    )


def create_short_drama_router(
    *,
    repository: ShortDramaRepository,
    pipeline: ShortDramaPipeline,
    dispatcher: NotificationDispatcher | None = None,
    scheduler: SchedulerStatus | None = None,
    page_path: Path | None = None,
    default_check_interval_minutes: int = 10,
) -> APIRouter:
    router = APIRouter()
    html_path = page_path or Path(__file__).with_name("short_drama.html")

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

    def health_payload() -> dict[str, str]:
        repository.counts()
        return {
            "status": "ok",
            "database": "ok",
            "scheduler": scheduler.health_status() if scheduler is not None else "not_started",
        }

    @router.get("/health")
    async def root_health() -> dict[str, str]:
        return health_payload()

    api = APIRouter(prefix="/api/short-drama", tags=["Short drama"])

    @api.get("/shows")
    async def list_shows(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        return {"shows": repository.list_show_summaries(limit=limit)}

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

    @api.post("/repair-consistency")
    async def repair_consistency() -> dict[str, Any]:
        return {"result": repository.repair_episode_and_show_consistency()}

    @api.get("/accounts")
    async def list_accounts() -> dict[str, Any]:
        return {"accounts": repository.list_accounts()}

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
    async def run_account_once(account_id: str) -> dict[str, Any]:
        try:
            if scheduler is not None:
                check = await scheduler.run_account_once(account_id)
                if not check.success:
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
            result = pipeline.reparse_account(
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
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return {
            "videos": repository.list_videos(
                needs_review=needs_review,
                classification_status=classification_status,
                limit=limit,
            )
        }

    @api.post("/videos/{video_id}/reparse")
    async def reparse_video(video_id: int) -> dict[str, Any]:
        try:
            result = pipeline.reparse_video(video_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "video": result.video,
            "status": result.status,
            "new_episode": result.new_episode,
        }

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
        result["enabled_notification_channels"] = list(dispatcher.enabled_channels) if dispatcher else []
        return result

    @api.get("/health")
    async def health() -> dict[str, str]:
        return health_payload()

    router.include_router(api)
    return router


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
