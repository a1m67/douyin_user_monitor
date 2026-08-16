"""Conservative regular-expression episode parser."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from douyin_user_monitor.parsers.base import (
    IGNORED,
    MATCHED,
    REVIEW,
    EpisodeParseInput,
    EpisodeParseResult,
)


_CHINESE_NUMERALS = "零〇一二三四五六七八九十百千万两"
_BRACKETED_TITLE_RE = re.compile(r"《\s*([^》]{2,80}?)\s*》")
_HASHTAG_RE = re.compile(r"#([\w\u4e00-\u9fff]{2,80})")
_BARE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_BARE_NUMBER_EXCLUSIONS = re.compile(
    r"^(?:万|万次|万播放|分钟|分(?:钟)?|种|个|条|年|月|天|小时|秒|倍|%|％|播放|点赞|粉丝|人|件|步|元)",
    re.IGNORECASE,
)
_GENERIC_TITLE_WORDS = frozenset(
    {
        "更新",
        "短剧",
        "全集",
        "推荐",
        "来了",
        "好看",
        "剧情",
        "追剧",
        "第",
        "今日",
        "热播",
    }
)
_GENERIC_SHOW_CANDIDATES = frozenset(
    {
        "ai动漫",
        "ai漫剧",
        "动漫",
        "动画",
        "二次元",
        "小云雀ai",
        "小云雀创作者计划",
        "ai创作浪潮计划",
        "自制动漫",
        "一口气看完",
        "原创ai漫剧",
        "原创ai新剧",
    }
)
_TRAILING_TITLE_NOISE_RE = re.compile(
    r"(?:更新|来了|持续更新|全集|短剧|追剧|热播|好看|完整版|正在播放|继续看).*?$",
    re.IGNORECASE,
)
_SHORT_DRAMA_CONTEXT_RE = re.compile(
    r"(?:短剧|剧场|追剧|全集|剧情|连续剧|漫剧|新剧|预告(?:片)?|先行)",
    re.IGNORECASE,
)
_DELIMITED_TITLE_SPLIT_RE = re.compile(r"[丨|｜]")
_PREFIX_SHOW_MARKER_RE = re.compile(r"^(.*?)\s+(?:我的)?(?:新剧|预告(?:片)?|漫剧)", re.IGNORECASE)
_SOURCE_PRIORITY = {
    "series_play_info.item_title_prefix.text": 400,
    "item_title": 300,
    "desc": 200,
    "description": 190,
}


@dataclass(frozen=True)
class _KnownTitle:
    show_id: int | None
    title: str
    normalized: str


@dataclass(frozen=True)
class _TextSource:
    field: str
    text: str
    priority: int


@dataclass(frozen=True)
class _EpisodePattern:
    name: str
    pattern: re.Pattern[str]
    priority: int


@dataclass(frozen=True)
class _ExplicitEpisodeMatch:
    source: _TextSource
    match: re.Match[str]
    pattern: _EpisodePattern


@dataclass(frozen=True)
class _EpisodeSignal:
    number: int
    source: _TextSource
    span: tuple[int, int]
    matched_text: str
    kind: str
    pattern: str
    score: int


@dataclass(frozen=True)
class _ShowSignal:
    title: str
    source: _TextSource
    span: tuple[int, int] | None
    matched_text: str
    kind: str


_EPISODE_PATTERNS = (
    _EpisodePattern(
        "chapter",
        re.compile(rf"第\s*([{_CHINESE_NUMERALS}\d]+)\s*集", re.IGNORECASE),
        100,
    ),
    _EpisodePattern("ep", re.compile(r"(?:\bep(?:isode)?\.?\s*)(\d+)", re.IGNORECASE), 98),
    _EpisodePattern("suffix", re.compile(r"(\d+)\s*(?:集|episode\b|ep\.?)", re.IGNORECASE), 96),
    _EpisodePattern("fraction", re.compile(r"(\d+)\s*(?:/|／|-|－)\s*\d+", re.IGNORECASE), 94),
)


class RegexParser:
    """Recognize explicit labels first, then score standalone numeric candidates."""

    def parse(self, request: EpisodeParseInput) -> EpisodeParseResult:
        sources = _text_sources(request)
        explicit = _find_explicit_episode_match(sources)
        if explicit is not None:
            episode_number = _parse_episode_number(explicit.match.group(1))
            if episode_number is None or episode_number <= 0:
                return EpisodeParseResult(
                    status=REVIEW,
                    show_title=None,
                    episode_number=None,
                    confidence=0.3,
                    reason="invalid_episode_number",
                    method="regex:invalid_episode_number",
                    content_type="unknown",
                    episode_evidence=_invalid_episode_evidence(explicit),
                )
            signal = _explicit_episode_signal(explicit, episode_number)
            return _explicit_episode_result(request, sources, signal)

        bare_episode = _find_bare_episode_candidate(sources)
        if bare_episode is not None:
            return _bare_episode_result(request, sources, bare_episode)
        return _without_episode_number(request, sources)


def normalize_title(value: str) -> str:
    """Normalize punctuation, brackets, spacing and casing for title matching."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    result: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if char.isspace() or category.startswith(("P", "S")):
            continue
        result.append(char)
    return "".join(result)


def chinese_number_to_int(value: str) -> int | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    if text.isdecimal():
        return int(text)
    digit_values = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    unit_values = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if any(char not in digit_values and char not in unit_values for char in text):
        return None

    total = 0
    section = 0
    number = 0
    for char in text:
        if char in digit_values:
            number = digit_values[char]
            continue
        unit = unit_values[char]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
            continue
        if number == 0:
            number = 1
        section += number * unit
        number = 0
    return total + section + number


def _text_sources(request: EpisodeParseInput) -> list[_TextSource]:
    result: list[_TextSource] = []
    for raw_field, raw_text in request.text_sources.items():
        field = str(raw_field or "").strip()
        text = str(raw_text or "").strip()
        if field and text:
            result.append(_TextSource(field=field, text=text, priority=_source_priority(field)))

    description = request.description.strip()
    if description and not any(source.text == description for source in result):
        result.append(
            _TextSource(field="description", text=description, priority=_source_priority("description"))
        )
    if not result:
        return [_TextSource(field="description", text="", priority=_source_priority("description"))]
    return sorted(result, key=lambda source: (-source.priority, source.field))


def _source_priority(field: str) -> int:
    return _SOURCE_PRIORITY.get(field, 100)


def _find_explicit_episode_match(sources: Sequence[_TextSource]) -> _ExplicitEpisodeMatch | None:
    candidates: list[_ExplicitEpisodeMatch] = []
    for source in sources:
        for pattern in _EPISODE_PATTERNS:
            for match in pattern.pattern.finditer(source.text):
                candidates.append(_ExplicitEpisodeMatch(source=source, match=match, pattern=pattern))
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.pattern.priority, item.source.priority, -item.match.start()),
    )


def _explicit_episode_signal(match: _ExplicitEpisodeMatch, number: int) -> _EpisodeSignal:
    return _EpisodeSignal(
        number=number,
        source=match.source,
        span=match.match.span(),
        matched_text=match.match.group(0),
        kind="explicit",
        pattern=match.pattern.name,
        score=match.pattern.priority,
    )


def _invalid_episode_evidence(match: _ExplicitEpisodeMatch) -> dict[str, Any]:
    return {
        "source_field": match.source.field,
        "matched_text": match.match.group(0),
        "kind": "explicit",
        "pattern": match.pattern.name,
    }


def _explicit_episode_result(
    request: EpisodeParseInput,
    sources: Sequence[_TextSource],
    signal: _EpisodeSignal,
) -> EpisodeParseResult:
    known_titles = _known_titles(request.known_shows)
    bracketed = _bracketed_title_signal(sources)
    if bracketed is not None:
        known = _find_known_title(normalize_title(bracketed.title), known_titles)
        if known is not None:
            return _matched_result(
                signal,
                known.title,
                0.99,
                "explicit_bracketed_title_and_episode",
                "regex:bracketed_known",
                known.show_id,
                _show_evidence(
                    known.title,
                    bracketed.source,
                    bracketed.matched_text,
                    "known_bracketed_title",
                    bracketed.span,
                ),
            )
        return _matched_result(
            signal,
            bracketed.title,
            0.97,
            "explicit_bracketed_title_and_episode",
            "regex:bracketed",
            show_evidence=_show_evidence(
                bracketed.title,
                bracketed.source,
                bracketed.matched_text,
                "bracketed_title",
                bracketed.span,
            ),
        )

    known_signal = _known_title_signal(sources, known_titles)
    if known_signal is not None:
        known, source = known_signal
        return _matched_result(
            signal,
            known.title,
            0.95,
            "known_show_and_episode",
            "regex:known_alias",
            known.show_id,
            _show_evidence(known.title, source, known.title, "known_title"),
        )

    cleaned = _title_before_episode(signal.source.text, signal.span)
    if cleaned:
        return _matched_result(
            signal,
            cleaned,
            0.86,
            "title_before_episode",
            "regex:cleaned_title",
            show_evidence=_show_evidence(cleaned, signal.source, cleaned, "title_before_episode"),
        )

    for tag, field in _all_hashtags(sources, request.hashtags):
        known = _find_known_title(normalize_title(tag), known_titles)
        source = _TextSource(field=field, text=tag, priority=_source_priority(field))
        if known is not None:
            return _matched_result(
                signal,
                known.title,
                0.93,
                "known_hashtag_and_episode",
                "regex:hashtag_known",
                known.show_id,
                _show_evidence(known.title, source, tag, "known_hashtag"),
            )
        candidate = _clean_title_candidate(tag)
        if candidate:
            return _matched_result(
                signal,
                candidate,
                0.81,
                "hashtag_and_episode",
                "regex:hashtag",
                show_evidence=_show_evidence(candidate, source, tag, "hashtag_title"),
            )

    return EpisodeParseResult(
        status=REVIEW,
        show_title=None,
        episode_number=signal.number,
        confidence=0.45,
        reason="episode_signal_without_reliable_title",
        method="regex:episode_without_title",
        episode_evidence=_episode_evidence(signal),
        content_type=_content_type(sources, request.hashtags),
    )


def _find_bare_episode_candidate(sources: Sequence[_TextSource]) -> _EpisodeSignal | None:
    """Score every valid bare integer instead of trusting the first one found."""
    candidates: list[_EpisodeSignal] = []
    for source in sources:
        for match in _BARE_NUMBER_RE.finditer(source.text):
            number = _parse_episode_number(match.group(1))
            if number is None or number <= 0:
                continue
            score = _bare_episode_score(source.text, match)
            if score is None:
                continue
            candidates.append(
                _EpisodeSignal(
                    number=number,
                    source=source,
                    span=match.span(),
                    matched_text=match.group(1),
                    kind="bare",
                    pattern="bare_integer",
                    score=score,
                )
            )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.score, item.source.priority, -item.span[0]),
    )


def _bare_episode_score(text: str, match: re.Match[str]) -> int | None:
    start, end = match.span()
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if (before and before in ".．") or (after and after in ".．"):
        return None
    if _is_ascii_letter(before) or _is_ascii_letter(after):
        return None
    if (before and before in ":：/／-－") or (after and after in ":：/／-－"):
        return None

    suffix = text[end:].lstrip()
    prefix = text[:start].rstrip()
    if _BARE_NUMBER_EXCLUSIONS.match(suffix) or prefix.endswith(("第", "约", "共")):
        return None

    has_han_context = _has_han(text[:start]) or _has_han(text[end:])
    if start == 0 or not prefix:
        return 90
    if before.isspace() and (not after or after.isspace() or _is_boundary(after)):
        return 78 if has_han_context else 58
    if _is_han(before) and (not after or after.isspace() or _is_boundary(after)):
        return 68
    if not after or _is_boundary(after):
        return 48
    return None


def _is_ascii_letter(value: str) -> bool:
    return bool(value) and value.isascii() and value.isalpha()


def _is_han(value: str) -> bool:
    return "\u4e00" <= value <= "\u9fff"


def _has_han(value: str) -> bool:
    return any(_is_han(char) for char in value)


def _is_boundary(value: str) -> bool:
    return value.isspace() or value in "，,。.!！?？、;；()（）[]【】《》|｜丨"


def _parse_episode_number(value: str) -> int | None:
    return chinese_number_to_int(value)


def _bracketed_title_signal(sources: Sequence[_TextSource]) -> _ShowSignal | None:
    for source in sources:
        for match in _BRACKETED_TITLE_RE.finditer(source.text):
            title = _clean_title_candidate(match.group(1))
            if title:
                return _ShowSignal(
                    title=title,
                    source=source,
                    span=match.span(),
                    matched_text=match.group(0),
                    kind="bracketed_title",
                )
    return None


def _known_titles(shows: Iterable[dict[str, Any]]) -> list[_KnownTitle]:
    result: list[_KnownTitle] = []
    for show in shows:
        title = str(show.get("title") or "").strip()
        if not title:
            continue
        raw_show_id = show.get("id")
        try:
            show_id = int(raw_show_id) if raw_show_id is not None else None
        except (TypeError, ValueError):
            show_id = None
        aliases = show.get("aliases")
        all_titles = [title]
        if isinstance(aliases, (list, tuple)):
            all_titles.extend(str(alias or "") for alias in aliases)
        normalized_title = normalize_title(title)
        for candidate in all_titles:
            normalized = normalize_title(candidate)
            if len(normalized) < 2:
                continue
            result.append(_KnownTitle(show_id=show_id, title=title, normalized=normalized))
        if normalized_title and not any(item.normalized == normalized_title for item in result):
            result.append(_KnownTitle(show_id=show_id, title=title, normalized=normalized_title))
    return sorted(result, key=lambda item: len(item.normalized), reverse=True)


def _find_known_title(normalized_haystack: str, known_titles: Iterable[_KnownTitle]) -> _KnownTitle | None:
    for candidate in known_titles:
        if candidate.normalized and candidate.normalized in normalized_haystack:
            return candidate
    return None


def _known_title_signal(
    sources: Sequence[_TextSource],
    known_titles: Iterable[_KnownTitle],
) -> tuple[_KnownTitle, _TextSource] | None:
    for source in sources:
        known = _find_known_title(normalize_title(source.text), known_titles)
        if known is not None:
            return known, source
    return None


def _title_before_episode(description: str, episode_span: tuple[int, int]) -> str | None:
    prefix = description[: episode_span[0]]
    prefix = _HASHTAG_RE.sub("", prefix)
    prefix = _TRAILING_TITLE_NOISE_RE.sub("", prefix)
    return _clean_title_candidate(prefix)


def _all_hashtags(sources: Sequence[_TextSource], hashtags: Iterable[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        for match in _HASHTAG_RE.finditer(source.text):
            candidate = match.group(1)
            if candidate and candidate not in seen:
                seen.add(candidate)
                result.append((candidate, source.field))
    for raw_tag in hashtags:
        candidate = str(raw_tag or "").strip().lstrip("#")
        if candidate and candidate not in seen:
            seen.add(candidate)
            result.append((candidate, "hashtags"))
    return result


def _clean_title_candidate(value: str) -> str | None:
    text = str(value or "").strip()
    text = text.strip("《》【】[]()（）<>〈〉#：:;；，,。.!！?？-_— ")
    text = _TRAILING_TITLE_NOISE_RE.sub("", text).strip("《》【】[]()（）<>〈〉#：:;；，,。.!！?？-_— ")
    normalized = normalize_title(text)
    if (
        len(normalized) < 2
        or normalized in _GENERIC_TITLE_WORDS
        or normalized in {normalize_title(item) for item in _GENERIC_SHOW_CANDIDATES}
    ):
        return None
    han_count = sum("\u4e00" <= char <= "\u9fff" for char in normalized)
    if han_count < 2:
        return None
    return text


def _without_episode_number(
    request: EpisodeParseInput,
    sources: Sequence[_TextSource],
) -> EpisodeParseResult:
    known_titles = _known_titles(request.known_shows)
    known_signal = _known_title_signal(sources, known_titles)
    if known_signal is not None:
        known, source = known_signal
        return EpisodeParseResult(
            status=REVIEW,
            show_title=known.title,
            episode_number=None,
            confidence=0.6,
            reason="known_show_without_episode",
            method="regex:known_alias_without_episode",
            matched_show_id=known.show_id,
            show_title_candidate=known.title,
            content_type=_content_type(sources, request.hashtags),
            show_evidence=_show_evidence(known.title, source, known.title, "known_title"),
        )
    for tag, field in _all_hashtags(sources, request.hashtags):
        known = _find_known_title(normalize_title(tag), known_titles)
        if known is not None:
            source = _TextSource(field=field, text=tag, priority=_source_priority(field))
            return EpisodeParseResult(
                status=REVIEW,
                show_title=known.title,
                episode_number=None,
                confidence=0.6,
                reason="known_show_without_episode",
                method="regex:known_hashtag_without_episode",
                matched_show_id=known.show_id,
                show_title_candidate=known.title,
                content_type=_content_type(sources, request.hashtags),
                show_evidence=_show_evidence(known.title, source, tag, "known_hashtag"),
            )

    bracketed = _bracketed_title_signal(sources)
    if bracketed is not None and _has_short_drama_context(request, sources):
        return EpisodeParseResult(
            status=REVIEW,
            show_title=bracketed.title,
            episode_number=None,
            confidence=0.52,
            reason="bracketed_title_with_short_drama_context_without_episode",
            method="regex:bracketed_without_episode",
            show_title_candidate=bracketed.title,
            content_type=_content_type(sources, request.hashtags),
            show_evidence=_show_evidence(
                bracketed.title,
                bracketed.source,
                bracketed.matched_text,
                "bracketed_title",
                bracketed.span,
            ),
        )

    candidate = _show_candidate_without_episode(request, sources)
    if candidate is not None:
        signal, method = candidate
        return EpisodeParseResult(
            status=REVIEW,
            show_title=signal.title,
            episode_number=None,
            confidence=0.5,
            reason="show_candidate_without_episode",
            method=method,
            show_title_candidate=signal.title,
            content_type=_content_type(sources, request.hashtags),
            show_evidence=_show_evidence(
                signal.title,
                signal.source,
                signal.matched_text,
                signal.kind,
                signal.span,
            ),
        )

    return EpisodeParseResult(
        status=IGNORED,
        show_title=None,
        episode_number=None,
        confidence=0.0,
        reason="no_short_drama_or_episode_signal",
        method="regex:no_short_drama_signal",
        content_type="non_drama",
    )


def _bare_episode_result(
    request: EpisodeParseInput,
    sources: Sequence[_TextSource],
    signal: _EpisodeSignal,
) -> EpisodeParseResult:
    known_signal = _known_title_signal(sources, _known_titles(request.known_shows))
    if known_signal is not None:
        known, source = known_signal
        title = known.title
        show_evidence = _show_evidence(known.title, source, known.title, "known_title")
        matched_show_id = known.show_id
    else:
        bracketed = _bracketed_title_signal(sources)
        if bracketed is not None:
            title = bracketed.title
            show_evidence = _show_evidence(
                bracketed.title,
                bracketed.source,
                bracketed.matched_text,
                "bracketed_title",
                bracketed.span,
            )
        else:
            title = _bare_title_candidate(signal.source.text, signal.span)
            show_evidence = (
                _show_evidence(title, signal.source, title, "bare_title_prefix") if title else None
            )
        matched_show_id = None
    return EpisodeParseResult(
        status=REVIEW,
        show_title=title,
        episode_number=None,
        confidence=0.46,
        reason="bare_episode_signal_without_show_context",
        method="regex:bare_episode_signal",
        matched_show_id=matched_show_id,
        show_title_candidate=title,
        episode_candidate=signal.number,
        content_type="unknown",
        episode_evidence=_episode_evidence(signal),
        show_evidence=show_evidence,
    )


def _bare_title_candidate(description: str, span: tuple[int, int]) -> str | None:
    raw_prefix = description[: span[0]]
    if not raw_prefix.strip() or not raw_prefix[-1].isspace():
        return None
    prefix = re.split(r"\s+", raw_prefix.strip(), maxsplit=1)[0]
    return _clean_title_candidate(prefix)


def _show_candidate_without_episode(
    request: EpisodeParseInput,
    sources: Sequence[_TextSource],
) -> tuple[_ShowSignal, str] | None:
    if not _has_short_drama_context(request, sources):
        return None
    for source in sources:
        prefix_match = _PREFIX_SHOW_MARKER_RE.search(source.text)
        if prefix_match:
            candidate = _clean_title_candidate(prefix_match.group(1))
            if candidate:
                return (
                    _ShowSignal(
                        title=candidate,
                        source=source,
                        span=prefix_match.span(1),
                        matched_text=prefix_match.group(1),
                        kind="show_prefix_candidate",
                    ),
                    "regex:show_prefix_candidate",
                )

        for part in _DELIMITED_TITLE_SPLIT_RE.split(source.text):
            candidate = _clean_title_candidate(part)
            if candidate:
                start = source.text.find(part)
                return (
                    _ShowSignal(
                        title=candidate,
                        source=source,
                        span=(start, start + len(part)) if start >= 0 else None,
                        matched_text=part,
                        kind="delimited_show_candidate",
                    ),
                    "regex:delimited_show_candidate",
                )

    for tag, field in _all_hashtags(sources, request.hashtags):
        candidate = _clean_title_candidate(tag)
        if candidate:
            source = _TextSource(field=field, text=tag, priority=_source_priority(field))
            return (
                _ShowSignal(
                    title=candidate,
                    source=source,
                    span=None,
                    matched_text=tag,
                    kind="hashtag_show_candidate",
                ),
                "regex:hashtag_show_candidate",
            )
    return None


def _content_type(sources: Sequence[_TextSource], hashtags: Iterable[str]) -> str:
    text = " ".join([*(source.text for source in sources), *(str(tag or "") for tag in hashtags)])
    return "trailer" if re.search(r"(?:预告|先行)", text, re.IGNORECASE) else "show_content"


def _has_short_drama_context(request: EpisodeParseInput, sources: Sequence[_TextSource]) -> bool:
    candidates = [*(source.text for source in sources), *request.hashtags]
    return any(_SHORT_DRAMA_CONTEXT_RE.search(str(candidate or "")) for candidate in candidates)


def _episode_evidence(signal: _EpisodeSignal) -> dict[str, Any]:
    return {
        "value": signal.number,
        "source_field": signal.source.field,
        "matched_text": signal.matched_text,
        "kind": signal.kind,
        "pattern": signal.pattern,
        "score": signal.score,
        "span": list(signal.span),
    }


def _show_evidence(
    title: str,
    source: _TextSource,
    matched_text: str,
    kind: str,
    span: tuple[int, int] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "value": title,
        "source_field": source.field,
        "matched_text": matched_text,
        "kind": kind,
    }
    if span is not None:
        result["span"] = list(span)
    return result


def _matched_result(
    signal: _EpisodeSignal,
    show_title: str,
    confidence: float,
    reason: str,
    method: str,
    show_id: int | None = None,
    show_evidence: dict[str, Any] | None = None,
) -> EpisodeParseResult:
    return EpisodeParseResult(
        status=MATCHED,
        show_title=show_title,
        episode_number=signal.number,
        confidence=confidence,
        reason=reason,
        method=method,
        matched_show_id=show_id,
        show_title_candidate=show_title,
        episode_candidate=signal.number,
        content_type="episode",
        episode_evidence=_episode_evidence(signal),
        show_evidence=show_evidence,
    )
