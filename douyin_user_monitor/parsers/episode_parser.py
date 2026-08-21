"""Service facade for the first-stage regex parser."""
from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Protocol

from douyin_user_monitor.parsers.base import (
    EpisodeParseInput,
    EpisodeParseResult,
    EpisodeParserBackend,
    ParsedEpisodeOutcome,
    ParseTrace,
)
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
        return self.parse_with_trace(
            display_title=display_title,
            description=description,
            hashtags=hashtags,
            account_nickname=account_nickname,
            known_shows=known_shows,
            recent_account_videos=recent_account_videos,
            recent_account_matches=recent_account_matches,
            account_show_candidates=account_show_candidates,
            text_sources=text_sources,
        ).result

    def parse_with_trace(
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
    ) -> ParsedEpisodeOutcome:
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
            return _outcome(regex_result, regex_result=regex_result)

        result = regex_result
        context_called = False
        if result.status != "matched":
            context_called = True
            result = self._context_backend.parse(request, result)
        if result.status == "matched" and result.confidence >= self._auto_accept_confidence:
            return _outcome(result, regex_result=regex_result, context_called=context_called)

        llm_called = False
        llm_latency_ms = 0
        if self._llm_backend is not None and _should_call_llm(result):
            llm_called = True
            started = time.perf_counter()
            result = self._llm_backend.parse(request, regex_result)
            llm_latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        return _outcome(
            result,
            regex_result=regex_result,
            context_called=context_called,
            llm_called=llm_called,
            llm_latency_ms=llm_latency_ms,
        )


def _should_call_llm(result: EpisodeParseResult) -> bool:
    """Exclude obvious ordinary videos while allowing ambiguous episode signals."""
    if result.status == "ignored":
        return False
    if result.status == "review":
        return True
    return result.confidence < 1.0


def _outcome(
    result: EpisodeParseResult,
    *,
    regex_result: EpisodeParseResult,
    context_called: bool = False,
    llm_called: bool = False,
    llm_latency_ms: int = 0,
) -> ParsedEpisodeOutcome:
    return ParsedEpisodeOutcome(
        result=result,
        trace=ParseTrace(
            regex_called=True,
            context_called=context_called,
            llm_called=llm_called,
            regex_method=regex_result.method,
            final_method=result.method,
            llm_latency_ms=llm_latency_ms,
            final_confidence=result.confidence,
            review_reason=result.reason if result.status == "review" else None,
        ),
    )
