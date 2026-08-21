"""Low-frequency online backup and bounded SQLite housekeeping worker."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from douyin_user_monitor.maintenance import backup_database, passive_wal_checkpoint
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceWorkerConfig:
    enabled: bool = True
    poll_seconds: float = 300.0
    backup_interval_hours: int = 24
    checkpoint_interval_hours: int = 6
    backup_retention_count: int = 14
    scan_run_retention_days: int = 30
    raw_json_prune_batch_size: int = 500


class MaintenanceWorker:
    def __init__(self, repository: ShortDramaRepository, config: MaintenanceWorkerConfig) -> None:
        self._repository = repository
        self._config = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_backup_at: datetime | None = self._latest_backup_time()
        self._last_checkpoint_at: datetime | None = None
        self._last_run_at: datetime | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if not self._config.enabled or (self._task is not None and not self._task.done()):
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="maintenance-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc) or exc.__class__.__name__
                logger.exception("automatic maintenance failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._config.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def run_once(self, *, now: datetime | None = None, force_backup: bool = False) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        removed = await asyncio.to_thread(
            self._repository.prune_scan_runs,
            retention_days=self._config.scan_run_retention_days,
        )
        compacted = await asyncio.to_thread(
            self._repository.compact_video_raw_payloads,
            limit=self._config.raw_json_prune_batch_size,
        )
        backup_path: Path | None = None
        if force_backup or self._due(self._last_backup_at, current, self._config.backup_interval_hours):
            backup_path = await asyncio.to_thread(
                backup_database,
                self._repository.database_path,
                retention_count=self._config.backup_retention_count,
            )
            self._last_backup_at = current
        checkpoint = None
        if self._due(self._last_checkpoint_at, current, self._config.checkpoint_interval_hours):
            checkpoint = await asyncio.to_thread(passive_wal_checkpoint, self._repository.database_path)
            self._last_checkpoint_at = current
        self._last_run_at = current
        return {"backup": backup_path.name if backup_path else None, "scan_runs_pruned": removed,
                "raw_payloads_compacted": compacted, "checkpoint": checkpoint}

    def health_status(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "running": self._task is not None and not self._task.done(),
            "last_run_at": self._iso(self._last_run_at),
            "last_backup_at": self._iso(self._last_backup_at),
            "last_checkpoint_at": self._iso(self._last_checkpoint_at),
            "last_error": self._last_error,
        }

    def _latest_backup_time(self) -> datetime | None:
        backup_dir = self._repository.database_path.parent / "backups"
        backups = list(backup_dir.glob("app-[0-9]*.db"))
        if not backups:
            return None
        return datetime.fromtimestamp(max(item.stat().st_mtime for item in backups), timezone.utc)

    @staticmethod
    def _due(previous: datetime | None, now: datetime, hours: int) -> bool:
        return previous is None or now - previous >= timedelta(hours=hours)

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="seconds") if value else None
