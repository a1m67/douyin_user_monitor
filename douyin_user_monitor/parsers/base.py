"""Contracts shared by current and future episode parser backends."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class EpisodeParseInput:
    description: str
    hashtags: tuple[str, ...]
    account_nickname: str
    known_shows: Sequence[dict[str, Any]]


@dataclass(frozen=True)
class EpisodeParseResult:
    is_episode: bool
    show_title: str | None
    episode_number: int | None
    confidence: float
    method: str
    matched_show_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_episode": self.is_episode,
            "show_title": self.show_title,
            "episode_number": self.episode_number,
            "confidence": self.confidence,
            "method": self.method,
            "matched_show_id": self.matched_show_id,
        }


class EpisodeParserBackend(Protocol):
    """A parser stage that can be replaced by LLM/OCR backends later."""

    def parse(self, request: EpisodeParseInput) -> EpisodeParseResult:
        ...
