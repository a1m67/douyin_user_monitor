import asyncio
import uuid
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Set

from douyin_user_monitor.monitor.crawler_protocol import MonitorCrawlerProtocol
from douyin_user_monitor.monitor.downloader import AwemeAssetDownloader
from douyin_user_monitor.monitor.history_sync import (
    HISTORY_SYNC_PAGE_SIZE,
    HISTORY_SYNC_STATUS_COMPLETED,
    HISTORY_SYNC_STATUS_IDLE,
    HISTORY_SYNC_STATUS_PAUSED,
    HISTORY_SYNC_STATUS_PENDING,
    build_history_sync_state,
    normalize_history_sync_state,
)
from douyin_user_monitor.monitor.cookie_liveness import CookieLivenessService
from douyin_user_monitor.monitor.notifier import MonitorNotifierProtocol, NoopMonitorNotifier
from douyin_user_monitor.monitor.profile_parser import ACCOUNT_STATUS_NORMAL
from douyin_user_monitor.monitor.schedule import (
    LOOP_ERROR_RETRY_SECONDS,
    MIN_INTERVAL_HOURS as SCHEDULE_MIN_INTERVAL_HOURS,
    MODE_COVERAGE,
    MODE_INTERVAL,
    build_interval_gaps,
    choose_coverage_delay,
    validate_options,
)
from douyin_user_monitor.monitor.storage import (
    DEFAULT_COVERAGE_HOURS,
    DEFAULT_INTERVAL_HOURS,
    DEFAULT_MODE,
    MonitorStorage,
    utc_now,
)
from douyin_user_monitor.monitor.user_lookup import find_user_by_id, find_user_by_sec_uid
from douyin_user_monitor.monitor.user_sync import UserSyncService

MIN_INTERVAL_HOURS = SCHEDULE_MIN_INTERVAL_HOURS
LEGACY_COOKIE_KEYS = (
    "cookie_last_updated_at",
    "cookie_last_updated_source",
    "cookie_last_preview",
    "cookie_last_length",
    "cookie_last_upstream_result",
)


class MonitorService:
    def __init__(
        self,
        crawler: MonitorCrawlerProtocol,
        storage: MonitorStorage,
        download_root: Path,
        notifier: Optional[MonitorNotifierProtocol] = None,
        cookie_liveness_service: Optional[CookieLivenessService] = None,
    ):
        self._crawler = crawler
        self._storage = storage
        self._downloader = AwemeAssetDownloader(download_root)
        self._notifier = notifier or NoopMonitorNotifier()
        self._cookie_liveness_service = cookie_liveness_service
        self._sync_service = UserSyncService(
            crawler=self._crawler,
            downloader=self._downloader,
            notifier=self._notifier,
        )

        self._run_lock = asyncio.Lock()
        self._loop_task: Optional[asyncio.Task] = None

        self._mode = DEFAULT_MODE
        self._interval_hours = DEFAULT_INTERVAL_HOURS
        self._coverage_hours = DEFAULT_COVERAGE_HOURS

        self._coverage_priority_user_ids: List[str] = []
        self._active_coverage_state: Optional[Dict[str, Any]] = None
        self._reset_runtime_state()

    def _reset_runtime_state(self) -> None:
        state = self._storage.load_state()
        monitoring = state["monitoring"]
        self._was_running = bool(monitoring.get("is_running", False))
        monitoring["is_running"] = False
        self._apply_runtime_from_monitoring(monitoring)
        monitoring["updated_at"] = utc_now()
        monitoring.pop("interval_seconds", None)
        monitoring.pop("random_min_seconds", None)
        monitoring.pop("random_max_seconds", None)
        self._drop_legacy_scan_keys(monitoring)
        self._storage.save_state(state)

    async def auto_resume(self) -> None:
        """Resume monitoring loop if it was running before restart."""
        if self._was_running:
            await self.start_monitoring(
                mode=self._mode,
                interval_hours=self._interval_hours,
                coverage_hours=self._coverage_hours,
            )

    def _apply_runtime_from_monitoring(self, monitoring: Dict[str, Any]) -> None:
        self._mode = str(monitoring.get("mode", DEFAULT_MODE))
        interval_hours = monitoring.get("interval_hours")
        if isinstance(interval_hours, (int, float)):
            self._interval_hours = float(interval_hours)
        else:
            legacy_seconds = monitoring.get("interval_seconds", DEFAULT_INTERVAL_HOURS * 3600.0)
            self._interval_hours = float(legacy_seconds) / 3600.0
        self._coverage_hours = float(monitoring.get("coverage_hours", DEFAULT_COVERAGE_HOURS))

    def list_users(self) -> List[Dict[str, Any]]:
        return self._storage.load_state()["users"]

    async def list_users_with_profile(self) -> List[Dict[str, Any]]:
        state = self._storage.load_state()
        changed = await self._hydrate_missing_user_profiles(state)
        if changed:
            self._storage.save_state(state)
        return state["users"]

    async def add_user(self, profile_url: str) -> Dict[str, Any]:
        result = await self.add_user_with_status(profile_url)
        if result["status"] == "exists":
            user = result["user"]
            raise ValueError(f"用户已存在: {user['nickname']} ({user['sec_user_id']})")
        return result["user"]

    async def add_user_with_status(self, profile_url: str) -> Dict[str, Any]:
        raw_profile_text = (profile_url or "").strip()
        if not raw_profile_text:
            raise ValueError("用户主页链接不能为空")

        sec_user_id = await self._crawler.get_sec_user_id(raw_profile_text)
        state = self._storage.load_state()
        existed = find_user_by_sec_uid(state["users"], sec_user_id)
        if existed is not None:
            changed = await self._hydrate_missing_user_profile(existed)
            if changed:
                self._storage.save_state(state)
            return {"status": "exists", "user": existed}

        try:
            profile = await self._sync_service.resolve_profile_snapshot(sec_user_id)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"用户主页可解析 sec_user_id={sec_user_id}，但获取用户资料失败: {exc}"
            ) from exc
        now = utc_now()
        user = {
            "id": str(uuid.uuid4()),
            "profile_url": raw_profile_text,
            "sec_user_id": sec_user_id,
            "nickname": profile.nickname,
            "avatar_url": profile.avatar_url,
            "account_status": profile.account_status,
            "account_status_label": profile.account_status_label,
            "account_status_reason": profile.account_status_reason,
            "account_status_updated_at": now,
            "enabled": True,
            "created_at": now,
            "updated_at": now,
            "last_checked_at": None,
            "last_download_at": None,
            "last_aweme_id": None,
            "downloaded_count": 0,
            "downloaded_aweme_ids": [],
            "download_records": [],
            "history_sync": build_history_sync_state(
                status=HISTORY_SYNC_STATUS_PENDING,
                page_size=HISTORY_SYNC_PAGE_SIZE,
            ),
            "last_error": None,
        }
        state["users"].append(user)
        self._storage.save_state(state)

        self._attach_user_to_active_coverage_state(user)
        self._enqueue_coverage_priority_user(user["id"])
        return {"status": "created", "user": user}

    async def _hydrate_missing_user_profiles(self, state: Dict[str, Any]) -> bool:
        changed = False
        for user in state["users"]:
            try:
                changed = await self._hydrate_missing_user_profile(user) or changed
            except Exception as exc:  # noqa: BLE001
                changed = self._record_profile_hydration_error(user, exc) or changed
        return changed

    async def _hydrate_missing_user_profile(self, user: Dict[str, Any]) -> bool:
        nickname = str(user.get("nickname") or "").strip()
        avatar_url = str(user.get("avatar_url") or "").strip()
        account_status = str(user.get("account_status") or ACCOUNT_STATUS_NORMAL).strip() or ACCOUNT_STATUS_NORMAL
        needs_avatar_refresh = not avatar_url and account_status == ACCOUNT_STATUS_NORMAL
        if nickname and not needs_avatar_refresh:
            return False

        sec_user_id = str(user.get("sec_user_id") or "").strip()
        if not sec_user_id:
            raise ValueError("已存在用户缺少 sec_user_id，无法补全资料")

        try:
            profile = await self._sync_service.resolve_profile_snapshot(sec_user_id)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"用户 sec_user_id={sec_user_id} 补全资料失败: {exc}"
            ) from exc
        return self._merge_profile_snapshot(user, profile)

    def _record_profile_hydration_error(self, user: Dict[str, Any], exc: Exception) -> bool:
        error_message = f"资料补全失败: {exc}"
        changed = user.get("last_error") != error_message
        user["last_error"] = error_message
        if changed:
            user["updated_at"] = utc_now()
        return changed

    def _merge_profile_snapshot(self, user: Dict[str, Any], profile: Any) -> bool:
        now = utc_now()
        changed = False
        # Preserve existing nickname when profile returns sec_user_id[:12] fallback
        # (happens for deleted/banned users whose API returns empty nickname)
        nickname = profile.nickname
        old_nickname = str(user.get("nickname") or "").strip()
        sec_user_id = str(user.get("sec_user_id") or "")
        if old_nickname and nickname == sec_user_id[:12]:
            nickname = old_nickname
        fields = {
            "nickname": nickname,
            "avatar_url": profile.avatar_url,
            "account_status": profile.account_status,
            "account_status_label": profile.account_status_label,
            "account_status_reason": profile.account_status_reason,
        }
        for key, value in fields.items():
            if user.get(key) != value:
                user[key] = value
                changed = True
        if user.get("account_status_updated_at") != now:
            user["account_status_updated_at"] = now
            changed = True
        if changed:
            user["updated_at"] = now
        return changed

    def remove_user(self, user_id: str) -> Dict[str, Any]:
        state = self._storage.load_state()
        matched = find_user_by_id(state["users"], user_id)
        if matched is None:
            raise ValueError("用户不存在")

        state["users"] = [item for item in state["users"] if item["id"] != user_id]
        self._coverage_priority_user_ids = [uid for uid in self._coverage_priority_user_ids if uid != user_id]
        self._storage.save_state(state)
        return matched

    def set_user_enabled(self, user_id: str, enabled: bool) -> Dict[str, Any]:
        state = self._storage.load_state()
        matched = find_user_by_id(state["users"], user_id)
        if matched is None:
            raise ValueError("用户不存在")

        matched["enabled"] = enabled
        matched["updated_at"] = utc_now()
        if not enabled:
            self._coverage_priority_user_ids = [uid for uid in self._coverage_priority_user_ids if uid != user_id]
        self._storage.save_state(state)
        return matched

    def start_user_history_backfill(self, user_id: str) -> Dict[str, Any]:
        state = self._storage.load_state()
        user = find_user_by_id(state["users"], user_id)
        if user is None:
            raise ValueError("用户不存在")

        self._activate_history_sync(user)
        self._storage.save_state(state)
        self._enqueue_coverage_priority_user(user_id)
        return user

    def pause_user_history_backfill(self, user_id: str) -> Dict[str, Any]:
        state = self._storage.load_state()
        user = find_user_by_id(state["users"], user_id)
        if user is None:
            raise ValueError("用户不存在")

        history_sync = normalize_history_sync_state(user)
        history_sync["status"] = HISTORY_SYNC_STATUS_PAUSED
        history_sync["updated_at"] = utc_now()
        user["updated_at"] = utc_now()
        self._storage.save_state(state)
        return user

    def resume_user_history_backfill(self, user_id: str) -> Dict[str, Any]:
        state = self._storage.load_state()
        user = find_user_by_id(state["users"], user_id)
        if user is None:
            raise ValueError("用户不存在")

        self._activate_history_sync(user, reset_completed=False)
        self._storage.save_state(state)
        self._enqueue_coverage_priority_user(user_id)
        return user

    async def start_monitoring(
        self,
        mode: str,
        interval_hours: float,
        coverage_hours: float,
    ) -> Dict[str, Any]:
        validate_options(
            mode=mode,
            interval_hours=interval_hours,
            coverage_hours=coverage_hours,
        )
        if self._loop_task and not self._loop_task.done():
            raise ValueError("监控已在运行中")

        self._mode = mode
        self._interval_hours = interval_hours
        self._coverage_hours = coverage_hours
        self._coverage_priority_user_ids = []

        await self._update_monitoring_state(is_running=True)
        self._loop_task = asyncio.create_task(self._monitor_loop(), name="douyin-monitor")
        return self.get_status()

    async def stop_monitoring(self) -> Dict[str, Any]:
        if self._loop_task is None:
            await self._update_monitoring_state(is_running=False)
            return self.get_status()

        task = self._loop_task
        self._loop_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        self._coverage_priority_user_ids = []
        await self._update_monitoring_state(is_running=False)
        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        state = self._storage.load_state()
        running = bool(self._loop_task and not self._loop_task.done())

        monitoring = state["monitoring"]
        monitoring["is_running"] = running
        monitoring["mode"] = self._mode
        monitoring["interval_hours"] = self._interval_hours
        monitoring["coverage_hours"] = self._coverage_hours
        monitoring.pop("interval_seconds", None)
        monitoring.pop("random_min_seconds", None)
        monitoring.pop("random_max_seconds", None)
        self._drop_legacy_scan_keys(monitoring)

        self._storage.save_state(state)
        return monitoring

    async def shutdown(self) -> None:
        await self.stop_monitoring()
        await self._close_resource(self._crawler)
        await self._close_resource(self._notifier)

    async def run_once(self) -> Dict[str, Any]:
        async with self._run_lock:
            state = self._storage.load_state()
            users = self._enabled_users(state)
            summary = await self._sync_service.sync_users(users, gaps=[])
            self._save_summary(state, summary)
            return summary

    async def run_user_once(self, user_id: str) -> Dict[str, Any]:
        async with self._run_lock:
            state = self._storage.load_state()
            user = find_user_by_id(state["users"], user_id)
            if user is None:
                raise ValueError("用户不存在")

            summary = {"checked_users": 1, "downloaded_items": 0, "errors": []}
            await self._sync_service.sync_one_user(user, summary)
            self._save_summary(state, summary)
            return summary

    async def run_user_history_backfill_once(self, user_id: str) -> Dict[str, Any]:
        async with self._run_lock:
            state = self._storage.load_state()
            user = find_user_by_id(state["users"], user_id)
            if user is None:
                raise ValueError("用户不存在")

            self._activate_history_sync(user, reset_completed=False)
            summary = {"checked_users": 0, "downloaded_items": 0, "errors": []}
            try:
                summary["downloaded_items"] = await self._sync_service.sync_history_backfill_step(user)
                user["last_error"] = None
            except Exception as exc:
                user["last_error"] = str(exc)
                summary["errors"].append(
                    {"user_id": user["id"], "nickname": user["nickname"], "error": str(exc)}
                )
            finally:
                user["last_checked_at"] = utc_now()
                user["updated_at"] = utc_now()
            self._save_summary(state, summary)
            return summary

    async def _monitor_loop(self) -> None:
        while True:
            try:
                if self._mode == MODE_INTERVAL:
                    await self._run_interval_cycle()
                    await asyncio.sleep(self._interval_hours * 3600.0)
                else:
                    delay = await self._run_coverage_cycle()
                    if delay > 0:
                        await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._save_loop_error(str(exc))
                await asyncio.sleep(LOOP_ERROR_RETRY_SECONDS)

    async def _run_interval_cycle(self) -> None:
        async with self._run_lock:
            state = self._storage.load_state()
            users = self._enabled_users(state)
            summary = await self._sync_service.sync_users(users, build_interval_gaps(len(users)))
            self._save_summary(state, summary)
            await self._maybe_run_cookie_liveness(state)

    async def _run_coverage_cycle(self) -> float:
        async with self._run_lock:
            state = self._storage.load_state()
            self._active_coverage_state = state
            try:
                pending_users = self._enabled_users(state)
                processed_user_ids: Set[str] = set()
                summary = {"checked_users": 0, "downloaded_items": 0, "errors": []}
                cycle_seconds = self._coverage_hours * 3600
                cycle_start = monotonic()

                while pending_users:
                    self._insert_priority_users_into_pending(state, pending_users, processed_user_ids)
                    user = pending_users.pop(0)
                    user_id = str(user.get("id") or "")
                    if not user.get("enabled", True) or user_id in processed_user_ids:
                        continue

                    summary["checked_users"] += 1
                    await self._sync_service.sync_one_user(user, summary)
                    processed_user_ids.add(user_id)

                    self._insert_priority_users_into_pending(state, pending_users, processed_user_ids)
                    delay = self._calculate_coverage_delay(
                        cycle_seconds=cycle_seconds,
                        cycle_start=cycle_start,
                        pending_count=len(pending_users),
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)

                self._save_summary(state, summary)
                await self._maybe_run_cookie_liveness(state)
                elapsed = monotonic() - cycle_start
                return max(0.0, cycle_seconds - elapsed)
            finally:
                self._active_coverage_state = None

    def _calculate_coverage_delay(self, *, cycle_seconds: float, cycle_start: float, pending_count: int) -> float:
        if pending_count <= 0:
            return 0.0

        remaining_seconds = max(0.0, cycle_seconds - (monotonic() - cycle_start))
        future_gap_count = pending_count - 1
        return choose_coverage_delay(
            remaining_seconds=remaining_seconds,
            future_gap_count=future_gap_count,
        )

    def _attach_user_to_active_coverage_state(self, user: Dict[str, Any]) -> None:
        state = self._active_coverage_state
        if state is None:
            return
        if find_user_by_id(state["users"], str(user.get("id") or "")) is not None:
            return
        state["users"].append(user)

    def _enqueue_coverage_priority_user(self, user_id: str) -> None:
        if self._mode != MODE_COVERAGE:
            return
        if self._loop_task is None or self._loop_task.done():
            return
        if user_id in self._coverage_priority_user_ids:
            return
        self._coverage_priority_user_ids.append(user_id)

    def _insert_priority_users_into_pending(
        self,
        state: Dict[str, Any],
        pending_users: List[Dict[str, Any]],
        processed_user_ids: Set[str],
    ) -> None:
        if not self._coverage_priority_user_ids:
            return

        users = state["users"]
        priority_users: List[Dict[str, Any]] = []
        priority_ids: Set[str] = set()
        for user_id in self._coverage_priority_user_ids:
            if user_id in processed_user_ids:
                continue

            user = find_user_by_id(users, user_id)
            if user is None or not user.get("enabled", True) or user_id in priority_ids:
                continue

            priority_users.append(user)
            priority_ids.add(user_id)

        self._coverage_priority_user_ids = []
        if not priority_users:
            return

        pending_users[:] = [
            *priority_users,
            *[user for user in pending_users if user.get("id") not in priority_ids],
        ]

    async def _maybe_run_cookie_liveness(self, state: Dict[str, Any]) -> None:
        service = self._cookie_liveness_service
        if service is None:
            return
        try:
            await service.maybe_run(state)
            self._storage.save_state(state)
        except Exception as exc:  # noqa: BLE001
            monitoring = state.setdefault("monitoring", {})
            record = monitoring.setdefault("cookie_liveness", {})
            record["last_error"] = str(exc)
            record["updated_at"] = utc_now()
            monitoring["updated_at"] = utc_now()
            self._storage.save_state(state)

    def _save_summary(self, state: Dict[str, Any], summary: Dict[str, Any]) -> None:
        monitoring = state["monitoring"]
        monitoring["last_run_at"] = utc_now()
        monitoring["last_run_result"] = summary
        monitoring["updated_at"] = utc_now()
        self._storage.save_state(state)

    def _save_loop_error(self, message: str) -> None:
        state = self._storage.load_state()
        monitoring = state["monitoring"]
        monitoring["last_run_at"] = utc_now()
        monitoring["last_run_result"] = {"loop_error": message}
        monitoring["updated_at"] = utc_now()
        self._storage.save_state(state)

    async def _update_monitoring_state(self, is_running: bool) -> None:
        state = self._storage.load_state()
        monitoring = state["monitoring"]
        monitoring["is_running"] = is_running
        monitoring["mode"] = self._mode
        monitoring["interval_hours"] = self._interval_hours
        monitoring["coverage_hours"] = self._coverage_hours
        monitoring["updated_at"] = utc_now()
        monitoring.pop("interval_seconds", None)
        monitoring.pop("random_min_seconds", None)
        monitoring.pop("random_max_seconds", None)
        self._drop_legacy_scan_keys(monitoring)
        self._storage.save_state(state)

    def _enabled_users(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [user for user in state["users"] if user.get("enabled", True)]

    def _drop_legacy_scan_keys(self, monitoring: Dict[str, Any]) -> None:
        for key in LEGACY_COOKIE_KEYS:
            monitoring.pop(key, None)

    def _activate_history_sync(
        self,
        user: Dict[str, Any],
        *,
        reset_completed: bool = True,
    ) -> None:
        history_sync = normalize_history_sync_state(user)
        restart_required = history_sync["status"] in {
            HISTORY_SYNC_STATUS_COMPLETED,
            HISTORY_SYNC_STATUS_IDLE,
        }
        should_reset = restart_required and reset_completed
        if should_reset:
            user["history_sync"] = build_history_sync_state(
                status=HISTORY_SYNC_STATUS_PENDING,
                page_size=int(history_sync["page_size"] or HISTORY_SYNC_PAGE_SIZE),
                started_at=utc_now(),
                updated_at=utc_now(),
            )
        else:
            history_sync["status"] = HISTORY_SYNC_STATUS_PENDING
            history_sync["has_more"] = True
            history_sync["started_at"] = history_sync.get("started_at") or utc_now()
            history_sync["updated_at"] = utc_now()
            history_sync["last_error"] = None
            history_sync["completed_at"] = None
        user["updated_at"] = utc_now()

    async def _close_resource(self, resource: Any) -> None:
        close_method = getattr(resource, "aclose", None)
        if close_method is None:
            return
        result = close_method()
        if asyncio.iscoroutine(result):
            await result
