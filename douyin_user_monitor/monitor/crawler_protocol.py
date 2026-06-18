from __future__ import annotations

from typing import Any, Dict, Protocol


class MonitorCrawlerProtocol(Protocol):
    async def get_sec_user_id(self, url: str) -> str:
        ...

    async def handler_user_profile(self, sec_user_id: str) -> Dict[str, Any]:
        ...

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int) -> Dict[str, Any]:
        ...

    async def fetch_one_video(self, aweme_id: str) -> Dict[str, Any]:
        ...

    async def get_douyin_headers(self) -> Dict[str, Dict[str, str]]:
        ...

    async def aclose(self) -> None:
        ...
