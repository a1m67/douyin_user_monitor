"""Service facade for the first-stage regex parser."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from douyin_user_monitor.parsers.base import EpisodeParseInput, EpisodeParseResult, EpisodeParserBackend
from douyin_user_monitor.parsers.context import ContextParser
from douyin_user_monitor.parsers.regex import RegexParser


class LLMParserBackend(Protocol):
    def parse(
        self,
        request: EpisodeParseInput,
        regex_result: EpisodeParseResult,
    ) -> EpisodeParseResult:
        ...


class EpisodeParser:
    """Parse short-drama episode metadata without depending on a crawler."""

    def __init__(
        self,
        backend: EpisodeParserBackend | None = None,
        *,
        context_backend: ContextParser | None = None,
        llm_backend: LLMParserBackend | None = None,
        auto_accept_confidence: float = 0.8,
    ):
        if not 0 <= auto_accept_confidence <= 1:
            raise ValueError("AUTO_ACCEPT_CONFIDENCE 必须在 0 到 1 之间")
        # Keep ``backend`` as the public compatibility hook for the current
        # deterministic stage. Context and optional LLM stages share the same
        # request contract, so callers never need special-case plumbing.
        self._backend = backend or RegexParser()
        self._context_backend = context_backend or ContextParser()
        self._llm_backend = llm_backend
        self._auto_accept_confidence = auto_accept_confidence

    def parse(
        self,
        *,
        display_title: str = "",
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
            display_title=str(display_title or ""),
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
        regex_result = self._backend.parse(request)
        if (
            regex_result.status == "matched"
            and regex_result.confidence >= self._auto_accept_confidence
        ):
            return regex_result

        result = regex_result
        if result.status != "matched":
            result = self._context_backend.parse(request, result)
        if result.status == "matched" and result.confidence >= self._auto_accept_confidence:
            return result

        if self._llm_backend is not None and _should_call_llm(result):
            return self._llm_backend.parse(request, regex_result)
        return result


def _should_call_llm(result: EpisodeParseResult) -> bool:
    """Exclude obvious ordinary videos while allowing ambiguous episode signals."""
    if result.status == "ignored":
        return False
    if result.status == "review":
        return True
    return result.confidence < 1.0
