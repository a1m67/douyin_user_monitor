from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import aiofiles
import httpx

DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_DESC_LENGTH = 48
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _sanitize(value: str, default: str = "untitled", max_len: int = MAX_DESC_LENGTH) -> str:
    cleaned = INVALID_FILENAME_CHARS.sub("_", (value or "").strip()).strip(" ._")
    return cleaned[:max_len] if cleaned else default


def _build_filename(post: Dict[str, Any]) -> str:
    ts = post.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts)
        date_str = dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        date_str = "unknown-date"
    shortcode = post.get("shortcode", "unknown")
    return f"{date_str}_{shortcode}"


class IgDownloader:
    """Instagram 媒体下载器。"""

    def __init__(self, download_root: Path):
        self._download_root = download_root
        self._download_root.mkdir(parents=True, exist_ok=True)

    async def download_post(self, post: Dict[str, Any], username: str) -> Dict[str, Any]:
        """下载单个帖子的所有媒体文件。"""
        user_folder = self._download_root / username
        user_folder.mkdir(parents=True, exist_ok=True)
        filename = _build_filename(post)

        if post.get("is_video") and post.get("video_url"):
            return await self._download_video(post, user_folder, filename)
        else:
            return await self._download_image(post, user_folder, filename)

    async def _download_video(self, post: Dict[str, Any], folder: Path, filename: str) -> Dict[str, Any]:
        file_path = folder / f"{filename}.mp4"
        if file_path.exists():
            return {
                "media_type": "video",
                "files": [str(file_path.relative_to(self._download_root))],
                "total_size_bytes": file_path.stat().st_size,
                "downloaded": False,
            }
        url = post["video_url"]
        size = await self._download_file(url, file_path)
        return {
            "media_type": "video",
            "files": [str(file_path.relative_to(self._download_root))],
            "total_size_bytes": size,
            "downloaded": True,
        }

    async def _download_image(self, post: Dict[str, Any], folder: Path, filename: str) -> Dict[str, Any]:
        url = post.get("display_url")
        if not url:
            return {"media_type": "image", "files": [], "total_size_bytes": 0, "downloaded": False}
        file_path = folder / f"{filename}.jpg"
        if file_path.exists():
            return {
                "media_type": "image",
                "files": [str(file_path.relative_to(self._download_root))],
                "total_size_bytes": file_path.stat().st_size,
                "downloaded": False,
            }
        size = await self._download_file(url, file_path)
        return {
            "media_type": "image",
            "files": [str(file_path.relative_to(self._download_root))],
            "total_size_bytes": size,
            "downloaded": True,
        }

    @staticmethod
    async def _download_file(url: str, path: Path) -> int:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            async with aiofiles.open(path, "wb") as f:
                await f.write(resp.content)
            return len(resp.content)
