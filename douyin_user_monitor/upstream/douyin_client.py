from __future__ import annotations

from typing import Any, Dict

import httpx

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.douyin.com/",
}


class UpstreamDouyinClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_douyin_headers(self) -> Dict[str, Dict[str, str]]:
        return {"headers": dict(DEFAULT_HEADERS)}

    async def get_sec_user_id(self, url: str) -> str:
        data = await self._get_upstream_data("/api/douyin/web/get_sec_user_id", params={"url": url})
        if not isinstance(data, str) or not data.strip():
            raise ValueError("上游返回的 sec_user_id 无效")
        return data

    async def handler_user_profile(self, sec_user_id: str) -> Dict[str, Any]:
        data = await self._get_upstream_data(
            "/api/douyin/web/handler_user_profile",
            params={"sec_user_id": sec_user_id},
        )
        if not isinstance(data, dict):
            raise ValueError("上游返回的用户信息格式无效")
        return data

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int) -> Dict[str, Any]:
        data = await self._get_upstream_data(
            "/api/douyin/web/fetch_user_post_videos",
            params={
                "sec_user_id": sec_user_id,
                "max_cursor": max_cursor,
                "count": count,
            },
        )
        if not isinstance(data, dict):
            raise ValueError("上游返回的作品列表格式无效")
        return data

    async def fetch_one_video(self, aweme_id: str) -> Dict[str, Any]:
        data = await self._get_upstream_data(
            "/api/douyin/web/fetch_one_video",
            params={"aweme_id": aweme_id},
        )
        if not isinstance(data, dict):
            raise ValueError("上游返回的作品详情格式无效")
        return data

    async def _get_upstream_data(self, path: str, *, params: Dict[str, Any]) -> Any:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"请求上游失败: {exc}") from exc

        payload = self._parse_json_response(response)
        self._validate_upstream_envelope(response, payload)
        return payload["data"]

    def _parse_json_response(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"上游返回非 JSON: status={response.status_code}") from exc

        if not isinstance(payload, dict):
            raise ValueError("上游返回结构无效: 根节点不是对象")
        return payload

    def _validate_upstream_envelope(self, response: httpx.Response, payload: Dict[str, Any]) -> None:
        code = int(payload.get("code", response.status_code))
        if response.status_code != 200 or code != 200:
            detail = payload.get("detail")
            message = ""
            if isinstance(detail, dict):
                message = str(detail.get("message") or detail)
            elif detail is not None:
                message = str(detail)
            if not message:
                message = str(payload.get("message") or "上游返回错误")
            raise RuntimeError(f"上游接口异常(status={response.status_code}, code={code}): {message}")

        if "data" not in payload:
            raise ValueError("上游响应缺少 data 字段")
