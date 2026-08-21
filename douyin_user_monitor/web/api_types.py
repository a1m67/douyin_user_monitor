"""Protocols and request models shared by short-drama API route modules."""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class SchedulerStatus(Protocol):
    def health_status(self) -> str: ...
    async def run_account_once(self, account_id: str, *, force: bool = False) -> Any: ...
    def crawler_status(self) -> dict[str, object]: ...


class HistoryBackfillWorkerControl(Protocol):
    def wake(self) -> None: ...
    def health_status(self) -> str: ...


class CookieManagerControl(Protocol):
    def status(self) -> dict[str, Any]: ...
    def save(self, value: object) -> dict[str, Any]: ...
    async def test(self) -> dict[str, Any]: ...


class MaintenanceWorkerStatus(Protocol):
    def health_status(self) -> dict[str, Any]: ...


class AddAccountPayload(BaseModel):
    homepage_url: str = Field(min_length=1)
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class UpdateAccountPayload(BaseModel):
    nickname: str | None = Field(default=None, min_length=1)
    homepage_url: str | None = None
    enabled: bool | None = None
    check_interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class UpdateShowPayload(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    aliases: list[str] | None = None
    status: str | None = None
    expected_episode_count: int | None = Field(default=None, ge=1, le=100000)


class UpdateShowSeasonPayload(BaseModel):
    expected_episode_count: int | None = Field(default=None, ge=1, le=100000)
    status: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class WatchProgressPayload(BaseModel):
    watched_episode_number: int = Field(ge=0, le=100000)


class IgnoreShowPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class MergeShowPayload(BaseModel):
    source_show_id: int = Field(gt=0)


class ReviewPayload(BaseModel):
    show_id: int | None = None
    new_show_title: str | None = Field(default=None, max_length=120)
    episode_number: int = Field(ge=0, le=100000)
    season_number: int = Field(default=1, ge=1, le=1000)
    learn_alias: bool = False


class BatchIgnoreReviewPayload(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=500)


class MoveEpisodePayload(BaseModel):
    target_show_id: int = Field(gt=0)
    season_number: int = Field(ge=1, le=1000)
    episode_number: int = Field(ge=0, le=100000)


class MoveEpisodeSourcePayload(MoveEpisodePayload):
    pass


class BatchEpisodeSeasonPayload(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=500)
    season_number: int = Field(ge=1, le=1000)


class BatchVideoPayload(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=500)


class ReparseAccountPayload(BaseModel):
    scope: str = Field(default="legacy_ignored", pattern="^(legacy_ignored|ignored|ignored_review)$")


class CookieUpdatePayload(BaseModel):
    cookie: Any
