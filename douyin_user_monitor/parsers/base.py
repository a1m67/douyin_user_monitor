"""Contracts shared by current and future episode parser backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


MATCHED = "matched"
IGNORED = "ignored"
REVIEW = "review"
PARSE_STATUSES = frozenset({MATCHED, IGNORED, REVIEW})


@dataclass(frozen=True)
class EpisodeParseInput:
    description: str
    hashtags: tuple[str, ...]
    account_nickname: str
    known_shows: Sequence[dict[str, Any]]


@dataclass(frozen=True)
class EpisodeParseResult:
    status: str
    show_title: str | None
    episode_number: int | None
    confidence: float
    reason: str
    method: str
    matched_show_id: int | None = None

    def __post_init__(self) -> None:
        if self.status not in PARSE_STATUSES:
            raise ValueError(f"无效的解析状态: {self.status}")

    @property
    def is_episode(self) -> bool:
        """Compatibility signal for callers that only need triage vs ignore."""
        return self.status != IGNORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "show_title": self.show_title,
            "episode_number": self.episode_number,
            "confidence": self.confidence,
            "reason": self.reason,
            "method": self.method,
            "matched_show_id": self.matched_show_id,
        }


class EpisodeParserBackend(Protocol):
    """A parser stage that can be replaced by LLM/OCR backends later."""

    def parse(self, request: EpisodeParseInput) -> EpisodeParseResult:
        ...
