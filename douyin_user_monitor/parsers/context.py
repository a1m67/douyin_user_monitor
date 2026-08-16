"""Account-context parser stage for resolving otherwise ambiguous episodes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from douyin_user_monitor.parsers.base import (
    MATCHED,
    REVIEW,
    EpisodeParseInput,
    EpisodeParseResult,
)
from douyin_user_monitor.parsers.regex import normalize_title


class ContextParser:
    """Resolve episode signals using recent account continuity.

    A bare number never creates a show by itself. The account must have one
    unambiguous active show with at least two consecutive recent episodes, or
    the description must reference an existing show/alias. Multiple active
    shows intentionally remain unresolved for review or a future LLM stage.
    """

    def parse(
        self,
        request: EpisodeParseInput,
        base_result: EpisodeParseResult,
    ) -> EpisodeParseResult:
        if base_result.status == MATCHED:
            return base_result

        candidate = base_result.episode_candidate or base_result.episode_number
        if candidate is None or candidate <= 0:
            return base_result

        known = _find_referenced_show(request, base_result)
        if known is not None:
            return _matched_from_context(
                show=known,
                episode_number=candidate,
                confidence=0.91 if base_result.episode_candidate else 0.94,
                reason="known_show_context_resolved_episode",
                method="context:known_show",
                base_result=base_result,
                evidence_kind="known_show_context",
            )

        inferred = _infer_account_show(request, candidate, base_result)
        if inferred is not None:
            show, confidence = inferred
            return _matched_from_context(
                show=show,
                episode_number=candidate,
                confidence=confidence,
                reason="account_sequence_resolved_episode",
                method="context:account_sequence",
                base_result=base_result,
                evidence_kind="account_sequence",
            )

        if base_result.episode_candidate is None:
            return EpisodeParseResult(
                status=REVIEW,
                show_title=base_result.show_title,
                episode_number=base_result.episode_number,
                confidence=min(base_result.confidence, 0.45),
                reason="episode_signal_without_show_context",
                method=base_result.method,
                matched_show_id=base_result.matched_show_id,
                show_title_candidate=base_result.show_title_candidate,
                episode_candidate=candidate,
                content_type=base_result.content_type,
                episode_evidence=base_result.episode_evidence,
                show_evidence=base_result.show_evidence,
            )
        return base_result


def _find_referenced_show(
    request: EpisodeParseInput,
    result: EpisodeParseResult,
) -> dict[str, Any] | None:
    haystack = normalize_title(
        " ".join(
            [
                request.description,
                *request.text_sources.values(),
                *request.hashtags,
                result.show_title_candidate or "",
                result.show_title or "",
            ]
        )
    )
    if not haystack:
        return None
    for show in _all_shows(request):
        title = str(show.get("title") or "").strip()
        if not title:
            continue
        candidates = [title]
        aliases = show.get("aliases")
        if isinstance(aliases, (list, tuple)):
            candidates.extend(str(alias or "") for alias in aliases)
        if any(
            len(normalize_title(candidate)) >= 2
            and normalize_title(candidate) in haystack
            for candidate in candidates
        ):
            return show
    return None


def _infer_account_show(
    request: EpisodeParseInput,
    episode_number: int,
    result: EpisodeParseResult,
) -> tuple[dict[str, Any], float] | None:
    matches = [
        match
        for match in request.recent_account_matches
        if _positive_int(match.get("episode_number"))
        and str(match.get("show_title") or match.get("title") or "").strip()
    ]
    if not matches:
        return None

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        key = _show_key(match)
        if key:
            grouped[key].append(match)

    # If the account is actively publishing more than one show, the nearest
    # episode number is not enough evidence to choose between them.
    if len(grouped) != 1:
        return None
    entries = next(iter(grouped.values()))
    numbers = sorted(
        number
        for item in entries
        if (number := _positive_int(item.get("episode_number"))) is not None
    )
    if len(numbers) < 2 or not any(right - left == 1 for left, right in zip(numbers, numbers[1:])):
        return None
    latest = max(numbers)
    distance = abs(episode_number - latest)
    if episode_number == latest + 1:
        confidence = 0.92
    elif distance <= 2:
        confidence = 0.84
    else:
        return None

    show = _show_from_match(entries[-1])
    candidate = result.show_title_candidate
    if candidate and not _candidate_matches_show(candidate, show):
        return None
    return show, confidence


def _all_shows(request: EpisodeParseInput) -> Iterable[dict[str, Any]]:
    seen: set[tuple[int | None, str]] = set()
    for show in (*request.known_shows, *request.account_show_candidates):
        key = (
            _optional_int(show.get("id")),
            normalize_title(str(show.get("title") or "")),
        )
        if key in seen or not key[1]:
            continue
        seen.add(key)
        yield show


def _show_key(value: dict[str, Any]) -> str:
    show_id = _optional_int(value.get("show_id") or value.get("id"))
    title = normalize_title(str(value.get("show_title") or value.get("title") or ""))
    return f"id:{show_id}" if show_id is not None else f"title:{title}"


def _show_from_match(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _optional_int(value.get("show_id") or value.get("id")),
        "title": str(value.get("show_title") or value.get("title") or "").strip(),
        "aliases": list(value.get("aliases") or []),
    }


def _candidate_matches_show(candidate: str, show: dict[str, Any]) -> bool:
    normalized = normalize_title(candidate)
    titles = [str(show.get("title") or ""), *(show.get("aliases") or [])]
    return any(normalized and normalized in normalize_title(title) for title in titles)


def _matched_from_context(
    *,
    show: dict[str, Any],
    episode_number: int,
    confidence: float,
    reason: str,
    method: str,
    base_result: EpisodeParseResult,
    evidence_kind: str,
) -> EpisodeParseResult:
    title = str(show.get("title") or "").strip() or None
    return EpisodeParseResult(
        status=MATCHED,
        show_title=title,
        episode_number=episode_number,
        confidence=confidence,
        reason=reason,
        method=method,
        matched_show_id=_optional_int(show.get("id")),
        show_title_candidate=title,
        episode_candidate=episode_number,
        content_type="episode",
        episode_evidence=base_result.episode_evidence,
        show_evidence=base_result.show_evidence
        or {
            "value": title,
            "source_field": "account_context",
            "matched_text": title,
            "kind": evidence_kind,
        },
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _positive_int(value: Any) -> int | None:
    number = _optional_int(value)
    return number if number is not None and number > 0 else None
