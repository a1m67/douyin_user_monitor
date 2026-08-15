"""Service facade for the first-stage regex parser."""
from __future__ import annotations

from typing import Any, Iterable

from douyin_user_monitor.parsers.base import EpisodeParseInput, EpisodeParseResult, EpisodeParserBackend
from douyin_user_monitor.parsers.regex import RegexParser


class EpisodeParser:
    """Parse short-drama episode metadata without depending on a crawler."""

    def __init__(self, backend: EpisodeParserBackend | None = None):
        self._backend = backend or RegexParser()

    def parse(
        self,
        *,
        description: str,
        hashtags: Iterable[str] = (),
        account_nickname: str = "",
        known_shows: Iterable[dict[str, Any]] = (),
    ) -> EpisodeParseResult:
        request = EpisodeParseInput(
            description=str(description or ""),
            hashtags=tuple(str(tag or "") for tag in hashtags),
            account_nickname=str(account_nickname or ""),
            known_shows=tuple(known_shows),
        )
        return self._backend.parse(request)
