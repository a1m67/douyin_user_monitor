from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from douyin_user_monitor.monitor.ig_crawler import IgCrawler
from douyin_user_monitor.monitor.ig_downloader import IgDownloader
from douyin_user_monitor.monitor.ig_storage import IgStorage, utc_now
from douyin_user_monitor.monitor.notifier import MonitorNotifierProtocol, NoopMonitorNotifier

# 历史回填状态常量
HS_PENDING = "pending"
HS_RUNNING = "running"
HS_PAUSED = "paused"
HS_COMPLETED = "completed"
HS_FAILED = "failed"
HS_IDLE = "idle"


class IgService:
    """Instagram 监控服务。"""

    def __init__(
        self,
        crawler: IgCrawler,
        downloader: IgDownloader,
        storage: IgStorage,
        notifier: Optional[MonitorNotifierProtocol] = None,
        request_delay: float = 8.0,
    ):
        self._crawler = crawler
        self._downloader = downloader
        self._storage = storage
        self._notifier = notifier or NoopMonitorNotifier()
        self._request_delay = request_delay
        self._task: Optional[asyncio.Task] = None

    async def add_user(self, username: str) -> Dict[str, Any]:
        """添加 Instagram 监控用户。"""
        existing = self._storage.find_user_by_username(username)
        if existing:
            raise ValueError(f"用户 {username} 已存在")

        profile = await self._crawler.get_user_profile(username)
        user = {
            "id": str(uuid.uuid4()),
            "platform": "instagram",
            "username": username,
            "full_name": profile.get("full_name", ""),
            "avatar_url": profile.get("profile_pic_url", ""),
            "bio": profile.get("biography", ""),
            "follower_count": profile.get("followers", 0),
            "post_count": profile.get("mediacount", 0),
            "enabled": True,
            "created_at": utc_now(),
            "last_checked_at": None,
            "last_download_at": None,
            "downloaded_count": 0,
            "downloaded_post_ids": [],
            "download_records": [],
            "last_error": None,
            "history_sync": {
                "status": HS_PENDING,
                "next_max_id": "",
                "has_more": True,
                "processed_pages": 0,
                "scanned_items": 0,
                "downloaded_items": 0,
                "started_at": None,
                "completed_at": None,
                "last_error": None,
            },
        }
        return self._storage.add_user(user)

    async def sync_one_user(self, user: Dict[str, Any], summary: Dict[str, Any]) -> None:
        """同步单个用户：检查新帖子并下载。"""
        username = user["username"]
        try:
            posts = await self._crawler.fetch_latest_posts(username, count=20)
            downloaded_ids = set(user.get("downloaded_post_ids", []))
            new_posts = [p for p in posts if str(p["mediaid"]) not in downloaded_ids]

            for post in new_posts:
                result = await self._downloader.download_post(post, username)
                record = {
                    "post_id": str(post["mediaid"]),
                    "shortcode": post.get("shortcode", ""),
                    "caption": (post.get("caption") or "")[:200],
                    "media_type": result["media_type"],
                    "is_video": post.get("is_video", False),
                    "like_count": post.get("like_count", 0),
                    "comment_count": post.get("comment_count", 0),
                    "posted_at": post.get("timestamp", ""),
                    "downloaded_at": utc_now(),
                    "files": result["files"],
                    "total_size_bytes": result["total_size_bytes"],
                }
                user.setdefault("download_records", []).append(record)
                user.setdefault("downloaded_post_ids", []).append(str(post["mediaid"]))
                user["downloaded_count"] = user.get("downloaded_count", 0) + 1
                summary["downloaded_items"] += 1

                await self._notifier.notify_download_completed(
                    user_nickname=username, record=record
                )
                await asyncio.sleep(self._request_delay)

            user["last_checked_at"] = utc_now()
            if new_posts:
                user["last_download_at"] = utc_now()
            user["last_error"] = None
            self._storage.update_user(user["id"], user)

        except Exception as exc:
            user["last_error"] = str(exc)
            self._storage.update_user(user["id"], {"last_error": str(exc)})
            summary["errors"].append({"username": username, "error": str(exc)})

    async def run_once(self) -> Dict[str, Any]:
        """手动执行一次全部用户同步。"""
        state = self._storage.load_state()
        users = [u for u in state["users"] if u.get("enabled")]
        summary = {"checked_users": 0, "downloaded_items": 0, "errors": []}
        for user in users:
            summary["checked_users"] += 1
            await self.sync_one_user(user, summary)
            await asyncio.sleep(self._request_delay)
        self._storage.update_monitoring({"last_run_at": utc_now(), "last_run_result": summary})
        return summary

    async def start_monitoring(self, interval_hours: float) -> Dict[str, Any]:
        """启动定时监控循环。"""
        if self._task and not self._task.done():
            return {"status": "already_running"}
        self._storage.update_monitoring({
            "is_running": True,
            "interval_hours": interval_hours,
        })
        self._task = asyncio.create_task(self._loop(interval_hours))
        return {"status": "started", "interval_hours": interval_hours}

    async def stop_monitoring(self) -> Dict[str, Any]:
        """停止监控循环。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._storage.update_monitoring({"is_running": False})
        return {"status": "stopped"}

    async def _loop(self, interval_hours: float) -> None:
        interval_seconds = interval_hours * 3600
        while True:
            try:
                await self.run_once()
            except Exception:
                pass
            await asyncio.sleep(interval_seconds)

    async def auto_resume(self) -> None:
        """服务启动时自动恢复监控。"""
        # 先加载 Instagram session
        try:
            await self._crawler.login()
            print(f"[Instagram] Session loaded for {self._crawler._login_user}")
        except Exception as e:
            print(f"[Instagram] Warning: Failed to load session: {e}")

        state = self._storage.load_state()
        monitoring = state.get("monitoring", {})
        if monitoring.get("is_running"):
            interval = monitoring.get("interval_hours", 6.0)
            await self.start_monitoring(interval)

    async def shutdown(self) -> None:
        """服务关闭时停止监控。"""
        await self.stop_monitoring()

    # ---- 历史回填 ----

    def start_history_backfill(self, user_id: str) -> Dict[str, Any]:
        """启动历史回填。"""
        user = self._storage.find_user_by_id(user_id)
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")
        hs = user.get("history_sync", {})
        if hs.get("status") in (HS_RUNNING,):
            raise ValueError("历史回填已在运行中")
        hs["status"] = HS_PENDING
        hs["started_at"] = utc_now()
        hs["last_error"] = None
        user["history_sync"] = hs
        return self._storage.update_user(user_id, {"history_sync": hs})

    def pause_history_backfill(self, user_id: str) -> Dict[str, Any]:
        """暂停历史回填。"""
        user = self._storage.find_user_by_id(user_id)
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")
        hs = user.get("history_sync", {})
        if hs.get("status") not in (HS_RUNNING, HS_PENDING, HS_FAILED, HS_IDLE):
            raise ValueError(f"当前状态 {hs.get('status')} 无法暂停")
        hs["status"] = HS_PAUSED
        user["history_sync"] = hs
        return self._storage.update_user(user_id, {"history_sync": hs})

    def resume_history_backfill(self, user_id: str) -> Dict[str, Any]:
        """恢复历史回填。"""
        user = self._storage.find_user_by_id(user_id)
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")
        hs = user.get("history_sync", {})
        if hs.get("status") not in (HS_PAUSED,):
            raise ValueError(f"当前状态 {hs.get('status')} 无法恢复")
        hs["status"] = HS_IDLE
        user["history_sync"] = hs
        return self._storage.update_user(user_id, {"history_sync": hs})

    async def run_history_backfill_once(self, user_id: str) -> Dict[str, Any]:
        """执行一页历史回填。"""
        user = self._storage.find_user_by_id(user_id)
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")

        hs = user.get("history_sync", {})
        status = hs.get("status", HS_IDLE)

        if status in (HS_COMPLETED, HS_PAUSED):
            return {"status": status, "message": "回填已完成或已暂停"}

        # 更新状态为运行中
        hs["status"] = HS_RUNNING
        if not hs.get("started_at"):
            hs["started_at"] = utc_now()
        self._storage.update_user(user_id, {"history_sync": hs})

        summary = {"new_posts": 0, "total_pages": hs.get("processed_pages", 0), "error": None}

        try:
            max_id = hs.get("next_max_id") or None
            page = await self._crawler.fetch_posts_page(user["username"], max_id=max_id, count=50)

            posts = page["posts"]
            downloaded_ids = set(user.get("downloaded_post_ids", []))
            new_posts = [p for p in posts if str(p["mediaid"]) not in downloaded_ids]

            for post in new_posts:
                result = await self._downloader.download_post(post, user["username"])
                record = {
                    "post_id": str(post["mediaid"]),
                    "shortcode": post.get("shortcode", ""),
                    "caption": (post.get("caption") or "")[:200],
                    "media_type": result["media_type"],
                    "is_video": post.get("is_video", False),
                    "like_count": post.get("like_count", 0),
                    "comment_count": post.get("comment_count", 0),
                    "posted_at": post.get("timestamp", ""),
                    "downloaded_at": utc_now(),
                    "files": result["files"],
                    "total_size_bytes": result["total_size_bytes"],
                }
                user.setdefault("download_records", []).append(record)
                user.setdefault("downloaded_post_ids", []).append(str(post["mediaid"]))
                user["downloaded_count"] = user.get("downloaded_count", 0) + 1
                hs["downloaded_items"] = hs.get("downloaded_items", 0) + 1
                summary["new_posts"] += 1

                await self._notifier.notify_download_completed(
                    user_nickname=user["username"], record=record
                )
                await asyncio.sleep(self._request_delay)

            # 更新回填状态
            hs["scanned_items"] = hs.get("scanned_items", 0) + len(posts)
            hs["processed_pages"] = hs.get("processed_pages", 0) + 1
            hs["has_more"] = page["has_more"]
            hs["next_max_id"] = page.get("next_max_id", "")
            hs["updated_at"] = utc_now()
            hs["last_error"] = None

            if not page["has_more"]:
                hs["status"] = HS_COMPLETED
                hs["completed_at"] = utc_now()
            else:
                hs["status"] = HS_IDLE

            summary["total_pages"] = hs["processed_pages"]

        except Exception as exc:
            hs["status"] = HS_FAILED
            hs["last_error"] = str(exc)
            summary["error"] = str(exc)

        user["history_sync"] = hs
        self._storage.update_user(user_id, {"history_sync": hs, "downloaded_count": user.get("downloaded_count", 0)})
        return summary

    def get_status(self) -> Dict[str, Any]:
        state = self._storage.load_state()
        return state.get("monitoring", {})

    def get_users(self) -> list:
        state = self._storage.load_state()
        return state.get("users", [])

    def get_user(self, user_id: str) -> Dict[str, Any]:
        user = self._storage.find_user_by_id(user_id)
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")
        return user

    def update_user_enabled(self, user_id: str, enabled: bool) -> Dict[str, Any]:
        user = self._storage.update_user(user_id, {"enabled": enabled})
        if not user:
            raise ValueError(f"用户 {user_id} 不存在")
        return user

    def remove_user(self, user_id: str) -> None:
        if not self._storage.remove_user(user_id):
            raise ValueError(f"用户 {user_id} 不存在")
