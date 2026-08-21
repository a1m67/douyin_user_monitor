"""FastAPI routes for the server-rendered short-drama dashboard."""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.maintenance import backup_database, doctor_database
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.web.api_serialization import history_backfill_result, reparse_result, sync_result
from douyin_user_monitor.web.api_types import (
    AddAccountPayload, BatchEpisodeSeasonPayload, BatchIgnoreReviewPayload,
    BatchVideoPayload, CookieManagerControl, CookieUpdatePayload,
    HistoryBackfillWorkerControl, IgnoreShowPayload, MergeShowPayload,
    MaintenanceWorkerStatus,
    MoveEpisodePayload, MoveEpisodeSourcePayload, ReparseAccountPayload,
    ReviewPayload, SchedulerStatus, UpdateAccountPayload, UpdateShowPayload,
    UpdateShowSeasonPayload, WatchProgressPayload,
)
from douyin_user_monitor.web.pages import create_page_router
from douyin_user_monitor.web.update_grouping import group_update_events


def create_short_drama_router(
    *,
    repository: ShortDramaRepository,
    pipeline: ShortDramaPipeline,
    dispatcher: NotificationDispatcher | None = None,
    scheduler: SchedulerStatus | None = None,
    history_backfill_worker: HistoryBackfillWorkerControl | None = None,
    cookie_manager: CookieManagerControl | None = None,
    maintenance_worker: MaintenanceWorkerStatus | None = None,
    page_path: Path | None = None,
    default_check_interval_minutes: int = 10,
    admin_api_token: str = "",
) -> APIRouter:
    router = APIRouter()
    router.include_router(
        create_page_router(
            page_path=page_path,
            default_check_interval_minutes=default_check_interval_minutes,
        )
    )

    def health_payload() -> dict[str, str]:
        repository.counts()
        return {
            "status": "ok",
            "database": "ok",
            "scheduler": _worker_state_label(scheduler),
            "history_backfill_worker": _worker_state_label(history_backfill_worker),
        }

    @router.get("/health")
    async def root_health() -> dict[str, str]:
        return health_payload()

    configured_admin_token = admin_api_token.strip()

    async def require_admin_token(request: Request) -> None:
        if getattr(request.state, "auth_mode", None) == "session":
            return
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
        result["groups"] = group_update_events(result["events"])
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
        state = repository.get_service_state(
            "last_doctor_at", "last_doctor_ok", "last_doctor_summary",
            "last_backup_at", "last_maintenance_at", "last_checkpoint_at",
        )
        snapshot = repository.diagnostics_snapshot()
        wal_path = Path(f"{path}-wal")
        return {"database": {"path": path.name, "size_bytes": path.stat().st_size,
                "wal_size_bytes": wal_path.stat().st_size if wal_path.is_file() else 0,
                "schema_version": repository.schema_version(),
                "database_latency_ms": snapshot["database_latency_ms"],
                "last_doctor_at": state.get("last_doctor_at"),
                "last_doctor_ok": state.get("last_doctor_ok"),
                "last_doctor_summary": state.get("last_doctor_summary"),
                "backup_count": len(list((path.parent / "backups").glob("app-*.db")))},
                "scheduler": _worker_health(scheduler),
                "crawler": scheduler.crawler_status() if scheduler and hasattr(scheduler,"crawler_status") else {},
                "cookie": cookie_manager.status() if cookie_manager else {"status":"not_configured"},
                "features": {"llm": "configured_or_disabled", "ocr": "configured_or_disabled"},
                "parser_metrics_24h": snapshot["parser_metrics_24h"],
                "queues": {key: snapshot[key] for key in ("notification_queue", "review_queue", "history_queue")},
                "workers": {
                    "scheduler": _worker_health(scheduler),
                    "history": _worker_health(history_backfill_worker),
                    "notification": _worker_health(dispatcher),
                    "maintenance": _worker_health(maintenance_worker),
                },
                "maintenance": {
                    **_worker_health(maintenance_worker),
                    "last_backup_at": state.get("last_backup_at"),
                    "last_maintenance_at": state.get("last_maintenance_at"),
                    "last_checkpoint_at": state.get("last_checkpoint_at"),
                }}

    @api.get("/quality")
    async def data_quality(stale_days: int = Query(default=30, ge=1, le=3650)) -> dict[str, Any]:
        return repository.data_quality_report(stale_days=stale_days)

    @api.post("/diagnostics/doctor")
    async def run_doctor() -> dict[str, Any]:
        report = doctor_database(repository.database_path)
        repository.set_service_state(
            last_doctor_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            last_doctor_ok=report.ok,
            last_doctor_summary=report.checks,
        )
        return {"ok": report.ok, "checks": report.checks}

    @api.post("/diagnostics/backup")
    async def create_backup() -> dict[str, Any]:
        path = backup_database(repository.database_path)
        repository.set_service_state(
            last_backup_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        )
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
        return {"result": sync_result(result)}

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
        return {"result": history_backfill_result(result)}

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
        return {"result": reparse_result(result)}

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
        result["maintenance"] = maintenance_worker.health_status() if maintenance_worker else {"enabled": False}
        return result

    @api.get("/health")
    async def health() -> dict[str, str]:
        return health_payload()

    router.include_router(api)
    return router


def _worker_health(worker: object | None) -> dict[str, Any]:
    if worker is None:
        return {"running": False, "last_success": None, "last_error": None}
    status = worker.health_status() if hasattr(worker, "health_status") else "stopped"
    if isinstance(status, dict):
        return {
            "running": bool(status.get("running")),
            "last_success": status.get("last_success") or status.get("last_run_at"),
            "last_error": status.get("last_error"),
            **status,
        }
    return {
        "running": status in {"ok", "running"},
        "last_success": None,
        "last_error": None,
    }


def _worker_state_label(worker: object | None) -> str:
    if worker is None:
        return "not_started"
    return "ok" if _worker_health(worker)["running"] else "stopped"
