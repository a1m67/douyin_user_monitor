"""Telegram implementation of the unified episode notifier."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from douyin_user_monitor.notifiers.base import EpisodeNotification

CHINA_TZ = timezone(timedelta(hours=8))


class TelegramNotifier:
    channel = "telegram"

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base: str = "https://api.telegram.org",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token.strip() or not chat_id.strip():
            raise ValueError("Telegram 通知需要 bot_token 和 chat_id")
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._api_base = api_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)
        self._owns_client = client is None

    async def send_episode_update(self, notification: EpisodeNotification) -> None:
        text = format_episode_update(notification)
        if notification.cover_url:
            response = await self._client.post(
                f"{self._api_base}/bot{self._bot_token}/sendPhoto",
                json={
                    "chat_id": self._chat_id,
                    "photo": notification.cover_url,
                    "caption": text,
                },
            )
        else:
            response = await self._client.post(
                f"{self._api_base}/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text},
            )
        _raise_if_telegram_error(response)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def format_episode_update(notification: EpisodeNotification) -> str:
    lines = [
        "🎬 短剧更新",
        "",
        f"《{notification.show_title}》",
        "",
        f"更新至：第 {notification.episode_number} 集",
        "",
        f"作者：{notification.account_nickname}",
        "发布时间：",
        _format_time(notification.published_at),
    ]
    if notification.video_url:
        lines.extend(["", "查看抖音：", notification.video_url])
    return "\n".join(lines)


def _raise_if_telegram_error(response: httpx.Response) -> None:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code == 200 and isinstance(payload, dict) and payload.get("ok") is True:
        return
    detail = payload.get("description") if isinstance(payload, dict) else response.text
    raise RuntimeError(f"Telegram 发送失败: status={response.status_code}, detail={detail or '-'}")


def _format_time(value: str | None) -> str:
    if not value:
        return "-"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
