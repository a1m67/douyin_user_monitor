from __future__ import annotations

import asyncio
import json
import pickle
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import instaloader


@dataclass
class IgProfile:
    """Instagram 用户资料（从 web API 获取）。"""
    username: str
    full_name: str = ""
    biography: str = ""
    followers: int = 0
    mediacount: int = 0
    profile_pic_url: str = ""


class IgCrawler:
    """Instagram 爬虫，基于 curl + instaloader。"""

    USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Mobile/15E148 Safari/604.1'
    IG_APP_ID = '936619743392459'

    def __init__(self, login_user: str, session_file: Path, request_delay: float = 8.0, proxy: str = None):
        self._login_user = login_user
        self._session_file = session_file
        self._request_delay = request_delay
        self._proxy = proxy
        self._cookies = {}
        self._L = instaloader.Instaloader(
            download_videos=False,
            download_video_thumbnails=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )
        self._logged_in = False

    async def login(self) -> None:
        """加载已有 session。"""
        await asyncio.to_thread(self._login_sync)

    def _login_sync(self) -> None:
        session_path = str(self._session_file)
        try:
            with open(session_path, "rb") as f:
                self._cookies = pickle.load(f)
            self._logged_in = True
            return
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # 尝试 instaloader 格式
        try:
            self._L.load_session_from_file(self._login_user, session_path)
            self._cookies = {
                k: v for k, v in self._L.context._session.cookies.items()
            }
            self._logged_in = True
            return
        except Exception:
            pass

        # 交互式登录
        print(f"Instagram session 文件不存在，请输入 {self._login_user} 的密码进行登录：")
        self._L.login(self._login_user, input("密码: "))
        self._L.save_session_to_file(session_path)
        self._cookies = {
            k: v for k, v in self._L.context._session.cookies.items()
        }
        self._logged_in = True
        print("Session 已保存到", session_path)

    def _build_cookie_header(self) -> str:
        """构建 Cookie 请求头。"""
        parts = []
        for k, v in self._cookies.items():
            if v:
                parts.append(f"{k}={v}")
        return "; ".join(parts)

    def _curl_get(self, url: str) -> Dict[str, Any]:
        """使用 curl 发送 GET 请求（避免 TLS 指纹检测）。"""
        cmd = [
            'curl', '-s', url,
            '-H', f'User-Agent: {self.USER_AGENT}',
            '-H', f'X-IG-App-ID: {self.IG_APP_ID}',
            '-H', f'Cookie: {self._build_cookie_header()}',
        ]
        if self._proxy:
            cmd.extend(['-x', self._proxy])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise ValueError(f"curl 请求失败: {result.stderr}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            if "429" in result.stdout or "rate" in result.stdout.lower():
                raise ValueError("Instagram API 限流(429)，请稍后重试")
            raise ValueError(f"响应解析失败: {result.stdout[:200]}")

    async def get_user_profile(self, username: str) -> Dict[str, Any]:
        """获取用户资料（通过 web API）。"""
        profile = await asyncio.to_thread(self._get_profile_sync, username)
        await asyncio.to_thread(asyncio.sleep, self._request_delay)
        return {
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography,
            "followers": profile.followers,
            "mediacount": profile.mediacount,
            "profile_pic_url": profile.profile_pic_url,
        }

    def _get_profile_sync(self, username: str) -> IgProfile:
        """通过 web API 获取用户资料。"""
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        data = self._curl_get(url)

        user = data.get("data", {}).get("user", {})
        if not user:
            raise ValueError(f"用户 {username} 不存在")

        return IgProfile(
            username=user.get("username", username),
            full_name=user.get("full_name", ""),
            biography=user.get("biography", ""),
            followers=user.get("edge_followed_by", {}).get("count", 0),
            mediacount=user.get("edge_owner_to_timeline_media", {}).get("count", 0),
            profile_pic_url=user.get("profile_pic_url_hd") or user.get("profile_pic_url", ""),
        )

    async def fetch_latest_posts(self, username: str, count: int = 20) -> List[Dict[str, Any]]:
        """获取最新 N 个帖子。"""
        posts = await asyncio.to_thread(self._fetch_posts_sync, username, count)
        await asyncio.to_thread(asyncio.sleep, self._request_delay)
        return posts

    def _fetch_posts_sync(self, username: str, count: int) -> List[Dict[str, Any]]:
        """通过 web API 获取最新帖子。"""
        user_id = self._get_user_id(username)
        feed_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count={count}"
        feed_data = self._curl_get(feed_url)
        items = feed_data.get("items", [])
        return [self._item_to_dict(item) for item in items[:count]]

    async def fetch_posts_page(self, username: str, max_id: str = None, count: int = 50) -> Dict[str, Any]:
        """获取一页帖子（支持分页）。返回 {"posts": [...], "has_more": bool, "next_max_id": str}"""
        result = await asyncio.to_thread(self._fetch_posts_page_sync, username, max_id, count)
        await asyncio.to_thread(asyncio.sleep, self._request_delay)
        return result

    def _get_user_id(self, username: str) -> str:
        """获取用户 ID。"""
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        data = self._curl_get(url)
        user = data.get("data", {}).get("user", {})
        user_id = user.get("id")
        if not user_id:
            raise ValueError(f"无法获取用户 {username} 的 ID")
        return user_id

    def _fetch_posts_page_sync(self, username: str, max_id: str = None, count: int = 50) -> Dict[str, Any]:
        """通过 web API 获取一页帖子（支持分页）。"""
        user_id = self._get_user_id(username)

        feed_url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count={count}"
        if max_id:
            feed_url += f"&max_id={max_id}"

        feed_data = self._curl_get(feed_url)
        items = feed_data.get("items", [])
        posts = [self._item_to_dict(item) for item in items]

        # Instagram API 分页：more_available 为 true 时表示还有更多
        has_more = feed_data.get("more_available", False)
        next_max_id = feed_data.get("next_max_id", "")

        return {
            "posts": posts,
            "has_more": has_more,
            "next_max_id": next_max_id,
        }

    @staticmethod
    def _item_to_dict(item: Dict[str, Any]) -> Dict[str, Any]:
        """将 API 返回的帖子数据转换为标准格式。"""
        image_versions = item.get("image_versions2", {}).get("candidates", [])
        video_versions = item.get("video_versions", [])

        return {
            "mediaid": item.get("pk", 0),
            "shortcode": item.get("code", ""),
            "caption": (item.get("caption", {}) or {}).get("text", ""),
            "is_video": item.get("media_type", 0) == 2,
            "video_url": video_versions[0]["url"] if video_versions else None,
            "display_url": image_versions[0]["url"] if image_versions else "",
            "like_count": item.get("like_count", 0),
            "comment_count": item.get("comment_count", 0),
            "timestamp": item.get("taken_at", 0),
            "typename": "GraphVideo" if item.get("media_type", 0) == 2 else "GraphImage",
        }
