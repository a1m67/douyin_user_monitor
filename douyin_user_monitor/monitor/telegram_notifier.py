from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import httpx

from douyin_user_monitor.monitor.notifier import MonitorNotifierProtocol

CHINA_TZ = timezone(timedelta(hours=8))


class TelegramNotifier(MonitorNotifierProtocol):
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base: str,
        timeout_seconds: float,
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True)

    async def notify_new_aweme_detected(self, *, user_nickname: str, aweme_detail: Dict[str, Any]) -> None:
        message = self._build_detected_message(user_nickname=user_nickname, aweme_detail=aweme_detail)
        await self._send_message(message)

    async def notify_download_completed(self, *, user_nickname: str, record: Dict[str, Any]) -> None:
        message = self._build_completed_message(user_nickname=user_nickname, record=record)
        await self._send_message(message)

    async def notify_account_status_changed(
        self,
        *,
        user_nickname: str,
        old_status: str,
        new_status: str,
        reason: str | None,
    ) -> None:
        message = self._build_status_changed_message(
            user_nickname=user_nickname,
            old_status=old_status,
            new_status=new_status,
            reason=reason,
        )
        await self._send_message(message)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send_message(self, text: str) -> None:
        response = await self._client.post(
            f"{self._api_base}/bot{self._bot_token}/sendMessage",
            json={"chat_id": self._chat_id, "text": text},
        )
        payload = self._parse_payload(response)
        if response.status_code != 200 or not bool(payload.get("ok", False)):
            detail = payload.get("description") or payload
            raise RuntimeError(f"Telegram 发送失败: status={response.status_code}, detail={detail}")

    def _parse_payload(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Telegram 返回非 JSON: status={response.status_code}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Telegram 返回数据格式无效")
        return payload

    def _build_detected_message(self, *, user_nickname: str, aweme_detail: Dict[str, Any]) -> str:
        image_count = self._extract_image_count(aweme_detail)
        media_label = self._format_media_label("image" if image_count > 0 else "video", image_count)
        aweme_id = str(aweme_detail.get("aweme_id", "")).strip() or "-"
        desc = str(aweme_detail.get("desc", "")).strip() or "-"
        create_time = self._format_create_time(aweme_detail.get("create_time"))
        lines = [
            "🎬 发现新作品",
            "",
            f"👤 用户: {user_nickname}",
            f"📝 描述: {desc}",
            f"📊 类型: {media_label}",
            f"🆔 ID: {aweme_id}",
            f"⏰ 时间: {create_time}",
        ]
        return "\n".join(lines)

    def _build_completed_message(self, *, user_nickname: str, record: Dict[str, Any]) -> str:
        media_type = str(record.get("media_type", "unknown")).strip()
        image_count = self._safe_int(record.get("image_count"))
        media_label = self._format_media_label(media_type, image_count)
        desc = str(record.get("desc", "")).strip() or "-"
        size_text = self._format_size_mb(self._safe_int(record.get("total_size_bytes")))
        downloaded_at = self._format_iso_time(str(record.get("downloaded_at", "")))
        lines = [
            "✅ 下载完成",
            "",
            f"👤 用户: {user_nickname}",
            f"📝 描述: {desc}",
            f"📊 类型: {media_label}",
            f"💾 大小: {size_text}",
            f"⏰ 时间: {downloaded_at}",
        ]
        return "\n".join(lines)

    _STATUS_LABELS = {
        "normal": "正常",
        "deleted": "已注销",
        "banned": "已封禁",
    }

    def _build_status_changed_message(
        self,
        *,
        user_nickname: str,
        old_status: str,
        new_status: str,
        reason: str | None,
    ) -> str:
        old_label = self._STATUS_LABELS.get(old_status, old_status)
        new_label = self._STATUS_LABELS.get(new_status, new_status)
        icon = "🚫" if new_status == "banned" else "⚠️"
        lines = [
            f"{icon} 账号状态变更",
            "",
            f"👤 用户: {user_nickname}",
            f"📊 状态: {old_label} → {new_label}",
        ]
        if reason:
            lines.append(f"📝 原因: {reason}")
        return "\n".join(lines)

    def _extract_image_count(self, aweme_detail: Dict[str, Any]) -> int:
        images = aweme_detail.get("images", [])
        if not isinstance(images, list):
            images = []
        image_post_info = aweme_detail.get("image_post_info", {})
        image_post_images = image_post_info.get("images", []) if isinstance(image_post_info, dict) else []
        if not isinstance(image_post_images, list):
            image_post_images = []
        return len(images) + len(image_post_images)

    def _format_media_label(self, media_type: str, image_count: int) -> str:
        if media_type == "image":
            return f"图片集 ({image_count}张)" if image_count > 0 else "图片集"
        if media_type == "video":
            return "视频"
        return "未知"

    def _format_create_time(self, value: Any) -> str:
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return "-"
        return datetime.fromtimestamp(timestamp, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def _format_iso_time(self, value: str) -> str:
        text = value.strip()
        if not text:
            return "-"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return text
        return parsed.astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def _format_size_mb(self, bytes_size: int) -> str:
        return f"{bytes_size / (1024 * 1024):.2f} MB"

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
