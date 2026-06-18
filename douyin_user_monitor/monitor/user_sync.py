import asyncio
from typing import Any, Dict, List, Set

from douyin_user_monitor.monitor.downloader import AwemeAssetDownloader
from douyin_user_monitor.monitor.history_sync import (
    ACTIVE_HISTORY_SYNC_STATUSES,
    complete_history_sync,
    normalize_history_sync_state,
    update_history_sync_progress,
)
from douyin_user_monitor.monitor.notifier import MonitorNotifierProtocol
from douyin_user_monitor.monitor.profile_parser import (
    UserProfileSnapshot,
    build_profile_snapshot_fields,
    extract_account_status,
    extract_avatar_url,
    extract_nickname,
)
from douyin_user_monitor.monitor.storage import utc_now
from douyin_user_monitor.monitor.crawler_protocol import MonitorCrawlerProtocol
from douyin_user_monitor.monitor.sync_utils import (
    format_publish_time,
    merge_download_records,
    merge_downloaded_ids,
    safe_int,
)

INCREMENTAL_FETCH_POSTS = 20


class UserSyncService:
    def __init__(
        self,
        crawler: MonitorCrawlerProtocol,
        downloader: AwemeAssetDownloader,
        notifier: MonitorNotifierProtocol,
    ):
        self._crawler = crawler
        self._downloader = downloader
        self._notifier = notifier

    async def resolve_profile_snapshot(self, sec_user_id: str) -> UserProfileSnapshot:
        profile_data = await self._crawler.handler_user_profile(sec_user_id)
        account_status = extract_account_status(profile_data)
        nickname = extract_nickname(profile_data) or sec_user_id[:12]
        avatar_url = extract_avatar_url(profile_data)
        return UserProfileSnapshot(
            nickname=nickname,
            avatar_url=avatar_url,
            account_status=str(account_status["account_status"]),
            account_status_label=str(account_status["account_status_label"]),
            account_status_reason=account_status["account_status_reason"],
        )

    async def sync_users(self, users: List[Dict[str, Any]], gaps: List[float]) -> Dict[str, Any]:
        summary = {"checked_users": 0, "downloaded_items": 0, "errors": []}
        for index, user in enumerate(users):
            summary["checked_users"] += 1
            await self.sync_one_user(user, summary)
            if index < len(gaps) and gaps[index] > 0:
                await asyncio.sleep(gaps[index])
        return summary

    async def sync_one_user(self, user: Dict[str, Any], summary: Dict[str, Any]) -> None:
        await self._sync_user_with_summary(user, summary)

    async def _sync_user_with_summary(self, user: Dict[str, Any], summary: Dict[str, Any]) -> None:
        try:
            user.update(
                build_profile_snapshot_fields(
                    await self.resolve_profile_snapshot(user["sec_user_id"]),
                    updated_at=utc_now(),
                )
            )
            count = await self._sync_user_latest(user)
            count += await self.sync_history_backfill_step(user)
            summary["downloaded_items"] += count
            user["last_error"] = None
        except Exception as exc:
            user["last_error"] = str(exc)
            summary["errors"].append(
                {"user_id": user["id"], "nickname": user["nickname"], "error": str(exc)}
            )
        finally:
            user["last_checked_at"] = utc_now()
            user["updated_at"] = utc_now()

    async def _sync_user_latest(self, user: Dict[str, Any]) -> int:
        aweme_list = (await self._fetch_posts_page(
            user["sec_user_id"],
            max_cursor=0,
            count=INCREMENTAL_FETCH_POSTS,
        ))["aweme_list"]
        if not aweme_list:
            return 0
        downloaded_ids = set(user.get("downloaded_aweme_ids", []))
        new_posts = self._select_new_posts(aweme_list, downloaded_ids)
        if not new_posts:
            user["last_aweme_id"] = str(aweme_list[0].get("aweme_id", ""))
            return 0

        headers = (await self._crawler.get_douyin_headers())["headers"]
        downloaded_records, _ = await self._download_posts(
            user=user,
            posts=new_posts,
            headers=headers,
            notify=True,
            continue_on_error=False,
        )
        return self._apply_download_results(
            user,
            downloaded_records,
            last_aweme_id=str(aweme_list[0].get("aweme_id", "")),
        )

    async def sync_history_backfill_step(self, user: Dict[str, Any]) -> int:
        history_sync = normalize_history_sync_state(user)
        if history_sync["status"] not in ACTIVE_HISTORY_SYNC_STATUSES or not bool(history_sync["has_more"]):
            return 0

        page = await self._fetch_posts_page(
            user["sec_user_id"],
            max_cursor=int(history_sync["next_cursor"]),
            count=int(history_sync["page_size"]),
        )
        aweme_list = page["aweme_list"]
        if not aweme_list:
            complete_history_sync(history_sync, now=utc_now())
            return 0

        downloaded_ids = set(user.get("downloaded_aweme_ids", []))
        new_posts = self._select_new_posts(aweme_list, downloaded_ids)
        downloaded_records: List[Dict[str, Any]] = []
        errors: List[str] = []
        if new_posts:
            headers = (await self._crawler.get_douyin_headers())["headers"]
            downloaded_records, errors = await self._download_posts(
                user=user,
                posts=new_posts,
                headers=headers,
                notify=False,
                continue_on_error=True,
            )

        downloaded_count = self._apply_download_results(user, downloaded_records)
        self._update_history_sync_progress(
            page=page,
            history_sync=history_sync,
            scanned_count=len(aweme_list),
            downloaded_count=downloaded_count,
            errors=errors,
        )
        return downloaded_count

    async def _download_posts(
        self,
        user: Dict[str, Any],
        posts: List[Dict[str, Any]],
        headers: Dict[str, str],
        *,
        notify: bool,
        continue_on_error: bool,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        downloaded_records: List[Dict[str, Any]] = []
        errors: List[str] = []
        for post in reversed(posts):
            aweme_id = str(post.get("aweme_id", ""))
            try:
                detail_data = await self._crawler.fetch_one_video(aweme_id)
                aweme_detail = detail_data.get("aweme_detail")
                if not isinstance(aweme_detail, dict):
                    raise ValueError(f"作品 {aweme_id} 缺少 aweme_detail")
                if notify:
                    await self._notifier.notify_new_aweme_detected(
                        user_nickname=user["nickname"],
                        aweme_detail=aweme_detail,
                    )
                asset_result = await self._downloader.download_aweme_assets(
                    aweme_id=aweme_id,
                    sec_user_id=user["sec_user_id"],
                    user_nickname=user["nickname"],
                    aweme_detail=aweme_detail,
                    headers=headers,
                )
                record = self._build_download_record(aweme_detail, asset_result)
                if notify:
                    await self._notifier.notify_download_completed(
                        user_nickname=user["nickname"],
                        record=record,
                    )
                downloaded_records.append(record)
            except Exception as exc:
                if not continue_on_error:
                    raise
                errors.append(f"{aweme_id or '-'}: {exc}")
        return downloaded_records, errors

    async def _fetch_posts_page(
        self,
        sec_user_id: str,
        *,
        max_cursor: int,
        count: int,
    ) -> Dict[str, Any]:
        data = await self._crawler.fetch_user_post_videos(
            sec_user_id,
            max_cursor=max_cursor,
            count=count,
        )
        aweme_list = self._normalize_aweme_list(data.get("aweme_list"))
        aweme_list.sort(key=lambda item: item.get("create_time", 0), reverse=True)
        return {**data, "aweme_list": aweme_list}

    def _normalize_aweme_list(self, raw_aweme_list: Any) -> List[Dict[str, Any]]:
        if raw_aweme_list is None:
            return []
        if not isinstance(raw_aweme_list, list):
            raise ValueError("接口返回的 aweme_list 不是列表")
        if not all(isinstance(item, dict) for item in raw_aweme_list):
            raise ValueError("接口返回的 aweme_list 项格式无效")
        return raw_aweme_list

    def _select_new_posts(self, aweme_list: List[Dict[str, Any]], downloaded_ids: Set[str]) -> List[Dict[str, Any]]:
        result = []
        for item in aweme_list:
            aweme_id = str(item.get("aweme_id", ""))
            if aweme_id and aweme_id not in downloaded_ids:
                result.append(item)
        return result

    def _apply_download_results(
        self,
        user: Dict[str, Any],
        downloaded_records: List[Dict[str, Any]],
        *,
        last_aweme_id: str | None = None,
    ) -> int:
        downloaded_post_ids = [
            str(record.get("aweme_id", "")).strip()
            for record in downloaded_records
            if str(record.get("aweme_id", "")).strip()
        ]
        if last_aweme_id is not None:
            user["last_aweme_id"] = last_aweme_id
        if not downloaded_post_ids:
            return 0

        user["last_download_at"] = utc_now()
        user["downloaded_count"] = int(user.get("downloaded_count", 0)) + len(downloaded_post_ids)
        user["downloaded_aweme_ids"] = merge_downloaded_ids(
            user.get("downloaded_aweme_ids", []),
            downloaded_post_ids,
        )
        user["download_records"] = merge_download_records(
            user.get("download_records", []),
            downloaded_records,
        )
        return len(downloaded_post_ids)

    def _update_history_sync_progress(
        self,
        *,
        history_sync: Dict[str, Any],
        page: Dict[str, Any],
        scanned_count: int,
        downloaded_count: int,
        errors: List[str],
    ) -> None:
        update_history_sync_progress(
            history_sync=history_sync,
            page=page,
            scanned_count=scanned_count,
            downloaded_count=downloaded_count,
            errors=errors,
            now=utc_now(),
        )

    def _build_download_record(self, aweme_detail: Dict[str, Any], asset_result: Dict[str, Any]) -> Dict[str, Any]:
        aweme_id = str(aweme_detail.get("aweme_id", "")).strip()
        media_type = str(asset_result.get("media_type", "")).strip() or "unknown"
        files = asset_result.get("files", [])
        if not isinstance(files, list):
            files = []
        record = {
            "aweme_id": aweme_id,
            "desc": str(aweme_detail.get("desc", "")).strip(),
            "publish_time": format_publish_time(aweme_detail.get("create_time")),
            "downloaded_at": utc_now(),
            "media_type": media_type,
            "image_count": safe_int(asset_result.get("image_count")),
            "downloaded_file_count": safe_int(asset_result.get("downloaded_file_count")),
            "existing_file_count": safe_int(asset_result.get("existing_file_count")),
            "total_size_bytes": safe_int(asset_result.get("total_size_bytes")),
            "files": [str(file_item) for file_item in files if str(file_item).strip()],
        }
        if media_type == "video":
            video_info = aweme_detail.get("video", {})
            duration = video_info.get("duration") if isinstance(video_info, dict) else 0
            record["video_duration_ms"] = safe_int(duration)
        return record
