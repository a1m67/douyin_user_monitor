from __future__ import annotations

from typing import Any, Dict, Protocol


class MonitorNotifierProtocol(Protocol):
    async def notify_new_aweme_detected(self, *, user_nickname: str, aweme_detail: Dict[str, Any]) -> None:
        ...

    async def notify_download_completed(self, *, user_nickname: str, record: Dict[str, Any]) -> None:
        ...


class NoopMonitorNotifier:
    async def notify_new_aweme_detected(self, *, user_nickname: str, aweme_detail: Dict[str, Any]) -> None:
        _ = user_nickname, aweme_detail

    async def notify_download_completed(self, *, user_nickname: str, record: Dict[str, Any]) -> None:
        _ = user_nickname, record
