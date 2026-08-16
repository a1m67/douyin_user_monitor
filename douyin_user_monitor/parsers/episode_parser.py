"""Service facade for the first-stage regex parser."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from douyin_user_monitor.parsers.base import EpisodeParseInput, EpisodeParseResult, EpisodeParserBackend
from douyin_user_monitor.parsers.context import ContextParser
from douyin_user_monitor.parsers.regex import RegexParser


class EpisodeParser:
    """Parse short-drama episode metadata without depending on a crawler."""

    def __init__(
        self,
        backend: EpisodeParserBackend | None = None,
        *,
        context_backend: ContextParser | None = None,
        llm_backend: EpisodeParserBackend | None = None,
    ):
        # Keep ``backend`` as the public compatibility hook for the current
        # deterministic stage. Context and optional LLM stages share the same
        # request contract, so callers never need special-case plumbing.
        self._backend = backend or RegexParser()
        self._context_backend = context_backend or ContextParser()
        self._llm_backend = llm_backend

    def parse(
        self,
        *,
        description: str,
        hashtags: Iterable[str] = (),
        account_nickname: str = "",
        known_shows: Iterable[dict[str, Any]] = (),
        recent_account_videos: Iterable[dict[str, Any]] = (),
        recent_account_matches: Iterable[dict[str, Any]] = (),
        account_show_candidates: Iterable[dict[str, Any]] = (),
        text_sources: Mapping[str, Any] | None = None,
    ) -> EpisodeParseResult:
        request = EpisodeParseInput(
            description=str(description or ""),
            hashtags=tuple(str(tag or "") for tag in hashtags),
            account_nickname=str(account_nickname or ""),
            known_shows=tuple(known_shows),
            recent_account_videos=tuple(recent_account_videos),
            recent_account_matches=tuple(recent_account_matches),
            account_show_candidates=tuple(account_show_candidates),
            text_sources={
                str(field or ""): str(text or "")
                for field, text in (text_sources or {}).items()
                if str(field or "").strip() and str(text or "").strip()
            },
        )
        result = self._backend.parse(request)
        if result.status != "matched":
            result = self._context_backend.parse(request, result)
        # LLM use is deliberately opt-in. It receives the full contextual
        # request only after deterministic stages still need a decision.
        if self._llm_backend is not None and result.status == "review":
            result = self._llm_backend.parse(request)
        return result
