"""Contracts shared by current and future episode parser backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


MATCHED = "matched"
IGNORED = "ignored"
REVIEW = "review"
PARSE_STATUSES = frozenset({MATCHED, IGNORED, REVIEW})
CONTENT_TYPES = frozenset({"episode", "trailer", "show_content", "unknown", "non_drama"})


@dataclass(frozen=True)
class EpisodeParseInput:
    display_title: str
    description: str
    hashtags: tuple[str, ...]
    account_nickname: str
    known_shows: Sequence[dict[str, Any]] = ()
    # These context windows are intentionally plain mappings. They let a future
    # parser backend consume the same data without importing repository classes.
    recent_account_videos: Sequence[dict[str, Any]] = ()
    recent_account_matches: Sequence[dict[str, Any]] = ()
    account_show_candidates: Sequence[dict[str, Any]] = ()
    text_sources: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodeParseResult:
    status: str
    show_title: str | None
    episode_number: int | None
    confidence: float
    reason: str
    method: str
    matched_show_id: int | None = None
    show_title_candidate: str | None = None
    episode_candidate: int | None = None
    content_type: str = "unknown"
    episode_evidence: Mapping[str, Any] | None = None
    show_evidence: Mapping[str, Any] | None = None
    regex_result: Mapping[str, Any] | None = None
    llm_result: Mapping[str, Any] | None = None
    llm_raw_result: Any | None = None
    season_number: int = 1

    def __post_init__(self) -> None:
        if self.status not in PARSE_STATUSES:
            raise ValueError(f"无效的解析状态: {self.status}")
        if self.content_type not in CONTENT_TYPES:
            raise ValueError(f"无效的内容类型: {self.content_type}")
        if self.episode_number is not None and self.episode_number < 0:
            raise ValueError("集数不能小于 0")
        if self.episode_candidate is not None and self.episode_candidate < 0:
            raise ValueError("候选集数不能小于 0")
        if self.season_number < 1:
            raise ValueError("季数不能小于 1")

    @property
    def is_episode(self) -> bool:
        """Compatibility signal for callers that only need triage vs ignore."""
        return self.status != IGNORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "show_title": self.show_title,
            "episode_number": self.episode_number,
            "season_number": self.season_number,
            "confidence": self.confidence,
            "reason": self.reason,
            "method": self.method,
            "matched_show_id": self.matched_show_id,
            "show_title_candidate": self.show_title_candidate,
            "episode_candidate": self.episode_candidate,
            "content_type": self.content_type,
            "episode_evidence": dict(self.episode_evidence or {}),
            "show_evidence": dict(self.show_evidence or {}),
            "regex_result": dict(self.regex_result or {}),
            "llm_result": dict(self.llm_result or {}),
            "llm_raw_result": self.llm_raw_result,
        }

    @property
    def evidence(self) -> dict[str, dict[str, Any]]:
        return {
            "episode": dict(self.episode_evidence or {}),
            "show": dict(self.show_evidence or {}),
            "regex_result": dict(self.regex_result or {}),
            "llm_result": dict(self.llm_result or {}),
        }


class EpisodeParserBackend(Protocol):
    """A parser stage that can be replaced by LLM/OCR backends later."""

    def parse(self, request: EpisodeParseInput) -> EpisodeParseResult:
        ...
