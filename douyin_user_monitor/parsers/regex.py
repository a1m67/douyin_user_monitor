"""Conservative regular-expression episode parser."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

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
_EPISODE_PATTERNS = (
    re.compile(rf"第\s*([{_CHINESE_NUMERALS}\d]+)\s*集", re.IGNORECASE),
    re.compile(r"(?:\bep(?:isode)?\.?\s*)(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:集|episode\b|ep\.?)", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:/|／|-|－)\s*\d+", re.IGNORECASE),
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
_TRAILING_TITLE_NOISE_RE = re.compile(
    r"(?:更新|来了|持续更新|全集|短剧|追剧|热播|好看|完整版|正在播放|继续看).*?$",
    re.IGNORECASE,
)
_SHORT_DRAMA_CONTEXT_RE = re.compile(r"(?:短剧|剧场|追剧|全集|剧情|连续剧)", re.IGNORECASE)


@dataclass(frozen=True)
class _KnownTitle:
    show_id: int | None
    title: str
    normalized: str


class RegexParser:
    """Recognize explicit episode labels and reliable title sources only."""

    def parse(self, request: EpisodeParseInput) -> EpisodeParseResult:
        description = request.description.strip()
        episode_match = _find_episode_match(description)
        if episode_match is None:
            return _without_episode_number(request, description)

        episode_number = _parse_episode_number(episode_match.group(1))
        if episode_number is None or episode_number <= 0:
            return EpisodeParseResult(
                status=REVIEW,
                show_title=None,
                episode_number=None,
                confidence=0.3,
                reason="invalid_episode_number",
                method="regex:invalid_episode_number",
            )

        known_titles = _known_titles(request.known_shows)
        bracketed = _bracketed_title(description)
        if bracketed:
            known = _find_known_title(bracketed, known_titles)
            if known:
                return _matched_result(
                    episode_number,
                    known.title,
                    0.99,
                    "explicit_bracketed_title_and_episode",
                    "regex:bracketed_known",
                    known.show_id,
                )
            return _matched_result(
                episode_number,
                bracketed,
                0.97,
                "explicit_bracketed_title_and_episode",
                "regex:bracketed",
            )

        known = _find_known_title(normalize_title(description), known_titles)
        if known:
            return _matched_result(
                episode_number,
                known.title,
                0.95,
                "known_show_and_episode",
                "regex:known_alias",
                known.show_id,
            )

        cleaned = _title_before_episode(description, episode_match.span())
        if cleaned:
            return _matched_result(
                episode_number,
                cleaned,
                0.86,
                "title_before_episode",
                "regex:cleaned_title",
            )

        for tag in _all_hashtags(description, request.hashtags):
            known = _find_known_title(normalize_title(tag), known_titles)
            if known:
                return _matched_result(
                    episode_number,
                    known.title,
                    0.93,
                    "known_hashtag_and_episode",
                    "regex:hashtag_known",
                    known.show_id,
                )
            candidate = _clean_title_candidate(tag)
            if candidate:
                return _matched_result(
                    episode_number,
                    candidate,
                    0.81,
                    "hashtag_and_episode",
                    "regex:hashtag",
                )

        return EpisodeParseResult(
            status=REVIEW,
            show_title=None,
            episode_number=episode_number,
            confidence=0.45,
            reason="episode_signal_without_reliable_title",
            method="regex:episode_without_title",
        )


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


def _find_episode_match(text: str) -> re.Match[str] | None:
    for pattern in _EPISODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match
    return None


def _parse_episode_number(value: str) -> int | None:
    return chinese_number_to_int(value)


def _bracketed_title(description: str) -> str | None:
    match = _BRACKETED_TITLE_RE.search(description)
    if not match:
        return None
    return _clean_title_candidate(match.group(1))


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


def _title_before_episode(description: str, episode_span: tuple[int, int]) -> str | None:
    prefix = description[: episode_span[0]]
    prefix = _HASHTAG_RE.sub("", prefix)
    prefix = _TRAILING_TITLE_NOISE_RE.sub("", prefix)
    return _clean_title_candidate(prefix)


def _all_hashtags(description: str, hashtags: Iterable[str]) -> list[str]:
    result: list[str] = []
    for match in _HASHTAG_RE.finditer(description):
        candidate = match.group(1)
        if candidate not in result:
            result.append(candidate)
    for raw_tag in hashtags:
        candidate = str(raw_tag or "").strip().lstrip("#")
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _clean_title_candidate(value: str) -> str | None:
    text = str(value or "").strip()
    text = text.strip("《》【】[]()（）<>〈〉#：:;；，,。.!！?？-_— ")
    text = _TRAILING_TITLE_NOISE_RE.sub("", text).strip("《》【】[]()（）<>〈〉#：:;；，,。.!！?？-_— ")
    normalized = normalize_title(text)
    if len(normalized) < 2 or normalized in _GENERIC_TITLE_WORDS:
        return None
    han_count = sum("\u4e00" <= char <= "\u9fff" for char in normalized)
    if han_count < 2:
        return None
    return text


def _without_episode_number(request: EpisodeParseInput, description: str) -> EpisodeParseResult:
    known_titles = _known_titles(request.known_shows)
    known = _find_known_title(normalize_title(description), known_titles)
    if known:
        return EpisodeParseResult(
            status=REVIEW,
            show_title=known.title,
            episode_number=None,
            confidence=0.6,
            reason="known_show_without_episode",
            method="regex:known_alias_without_episode",
            matched_show_id=known.show_id,
        )
    for tag in _all_hashtags(description, request.hashtags):
        known = _find_known_title(normalize_title(tag), known_titles)
        if known:
            return EpisodeParseResult(
                status=REVIEW,
                show_title=known.title,
                episode_number=None,
                confidence=0.6,
                reason="known_show_without_episode",
                method="regex:known_hashtag_without_episode",
                matched_show_id=known.show_id,
            )

    bracketed = _bracketed_title(description)
    if bracketed and _has_short_drama_context(request, description):
        return EpisodeParseResult(
            status=REVIEW,
            show_title=bracketed,
            episode_number=None,
            confidence=0.52,
            reason="bracketed_title_with_short_drama_context_without_episode",
            method="regex:bracketed_without_episode",
        )

    return EpisodeParseResult(
        status=IGNORED,
        show_title=None,
        episode_number=None,
        confidence=0.0,
        reason="no_short_drama_or_episode_signal",
        method="regex:no_short_drama_signal",
    )


def _has_short_drama_context(request: EpisodeParseInput, description: str) -> bool:
    candidates = [description, request.account_nickname, *request.hashtags]
    return any(_SHORT_DRAMA_CONTEXT_RE.search(str(candidate or "")) for candidate in candidates)


def _matched_result(
    episode_number: int,
    show_title: str,
    confidence: float,
    reason: str,
    method: str,
    show_id: int | None = None,
) -> EpisodeParseResult:
    return EpisodeParseResult(
        status=MATCHED,
        show_title=show_title,
        episode_number=episode_number,
        confidence=confidence,
        reason=reason,
        method=method,
        matched_show_id=show_id,
    )
