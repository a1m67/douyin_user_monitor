"""Feishu incoming-webhook implementation of the episode notifier."""
from __future__ import annotations

import httpx

from douyin_user_monitor.notifiers.base import EpisodeNotification
from douyin_user_monitor.notifiers.telegram import format_episode_update


class FeishuNotifier:
    channel = "feishu"

    def __init__(
        self,
        *,
        webhook_url: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not webhook_url.strip():
            raise ValueError("飞书通知需要 webhook_url")
        self._webhook_url = webhook_url.strip()
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)
        self._owns_client = client is None

    async def send_episode_update(self, notification: EpisodeNotification) -> None:
        response = await self._client.post(
            self._webhook_url,
            json={"msg_type": "text", "content": {"text": format_episode_update(notification)}},
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"飞书通知发送失败: status={response.status_code}, detail={response.text[:500]}")
        try:
            payload = response.json()
        except ValueError:
            return
        if isinstance(payload, dict) and payload.get("code") not in (None, 0):
            raise RuntimeError(f"飞书通知发送失败: code={payload.get('code')}, msg={payload.get('msg')}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
