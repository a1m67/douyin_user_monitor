from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from douyin_user_monitor.short_drama_settings import _cookie_header_from_json


class CookieManager:
    def __init__(self, cookie_file: Path, *, reload_cookie: Callable[[str], None],
                 test_cookie: Callable[[], Awaitable[dict[str, Any]]] | None = None):
        self._path = Path(cookie_file)
        self._reload = reload_cookie
        self._test = test_cookie
        self._last_validation: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        configured = self._path.is_file() and self._path.stat().st_size > 0
        return {
            "configured": configured,
            "status": (self._last_validation or {}).get("status", "unknown" if configured else "not_configured"),
            "last_validated_at": (self._last_validation or {}).get("checked_at"),
            "last_updated_at": datetime.fromtimestamp(self._path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds") if configured else None,
        }

    def save(self, value: object) -> dict[str, Any]:
        cookie = self._parse(value)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent)
        temp_path = Path(raw_temp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"cookie": cookie}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self._path)
            self._reload(cookie)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        return self.status()

    async def test(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.status()["configured"]:
            result = {"status": "not_configured", "reason": "未配置 Cookie", "checked_at": checked_at}
        elif self._test is None:
            result = {"status": "unknown", "reason": "没有可用的启用账号", "checked_at": checked_at}
        else:
            try:
                payload = await self._test()
                result = {"status": str(payload.get("status") or "healthy"),
                          "reason": str(payload.get("reason") or "验证请求成功"), "checked_at": checked_at}
            except Exception as exc:
                message = str(exc).lower()
                status = "expired" if "login" in message or "cookie" in message else "risk_control" if "risk" in message or "风控" in message else "network_error"
                result = {"status": status, "reason": "Cookie 验证失败" if status == "expired" else "验证请求失败", "checked_at": checked_at}
        self._last_validation = result
        return dict(result)

    @staticmethod
    def _parse(value: object) -> str:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("[") or text.startswith("{"):
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError("Cookie JSON 格式无效") from exc
            else:
                cookie = text
                if "\r" in cookie or "\n" in cookie or "=" not in cookie:
                    raise ValueError("Cookie header 格式无效")
                return cookie
        cookie = _cookie_header_from_json(value)
        if not cookie or "=" not in cookie:
            raise ValueError("Cookie JSON 中没有有效的 name/value")
        return cookie
