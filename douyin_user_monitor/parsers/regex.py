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
            bare_episode = _find_bare_episode_candidate(description)
            if bare_episode is not None:
                return _bare_episode_result(request, description, bare_episode)
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
                content_type="unknown",
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


def _find_bare_episode_candidate(text: str) -> tuple[int, tuple[int, int]] | None:
    """Find a standalone number that may be an episode, without accepting it."""
    for match in _BARE_NUMBER_RE.finditer(text):
        number = _parse_episode_number(match.group(1))
        if number is None or number <= 0:
            continue
        suffix = text[match.end() :].lstrip()
        if _BARE_NUMBER_EXCLUSIONS.match(suffix):
            continue
        prefix = text[: match.start()].rstrip()
        if prefix.endswith(("第", "约", "共")):
            continue
        return number, match.span()
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
            show_title_candidate=known.title,
            content_type=_content_type(description),
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
                show_title_candidate=known.title,
                content_type=_content_type(description),
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
            show_title_candidate=bracketed,
            content_type=_content_type(description),
        )

    candidate = _show_candidate_without_episode(request, description)
    if candidate is not None:
        title, method = candidate
        return EpisodeParseResult(
            status=REVIEW,
            show_title=title,
            episode_number=None,
            confidence=0.5,
            reason="show_candidate_without_episode",
            method=method,
            show_title_candidate=title,
            content_type=_content_type(description),
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
    description: str,
    signal: tuple[int, tuple[int, int]],
) -> EpisodeParseResult:
    number, span = signal
    known = _find_known_title(normalize_title(description), _known_titles(request.known_shows))
    title = known.title if known else _bare_title_candidate(description, span)
    return EpisodeParseResult(
        status=REVIEW,
        show_title=title,
        episode_number=None,
        confidence=0.46,
        reason="bare_episode_signal_without_show_context",
        method="regex:bare_episode_signal",
        matched_show_id=known.show_id if known else None,
        show_title_candidate=title,
        episode_candidate=number,
        content_type="unknown",
    )


def _bare_title_candidate(description: str, span: tuple[int, int]) -> str | None:
    prefix = description[: span[0]].strip()
    if not prefix or (prefix and not prefix[-1].isspace()):
        return None
    # Prefer the first phrase before a role/person name. Known shows are
    # resolved before this heuristic, so this only supplies a review hint.
    prefix = re.split(r"\s+", prefix, maxsplit=1)[0]
    return _clean_title_candidate(prefix)


def _show_candidate_without_episode(
    request: EpisodeParseInput,
    description: str,
) -> tuple[str, str] | None:
    if not _has_short_drama_context(request, description):
        return None

    prefix_match = _PREFIX_SHOW_MARKER_RE.search(description)
    if prefix_match:
        candidate = _clean_title_candidate(prefix_match.group(1))
        if candidate:
            return candidate, "regex:show_prefix_candidate"

    parts = [_clean_title_candidate(part) for part in _DELIMITED_TITLE_SPLIT_RE.split(description)]
    parts = [part for part in parts if part]
    if len(parts) >= 2:
        return parts[0 if parts[0] else 1], "regex:delimited_show_candidate"

    for tag in _all_hashtags(description, request.hashtags):
        candidate = _clean_title_candidate(tag)
        if candidate:
            return candidate, "regex:hashtag_show_candidate"
    return None


def _content_type(description: str) -> str:
    return "trailer" if re.search(r"(?:预告|先行)", description, re.IGNORECASE) else "show_content"


def _has_short_drama_context(request: EpisodeParseInput, description: str) -> bool:
    # A generic account nickname such as "AI剧场" is too weak on its own:
    # otherwise ordinary posts with unrelated hashtags become review items.
    candidates = [description, *request.hashtags]
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
        show_title_candidate=show_title,
        episode_candidate=episode_number,
        content_type="episode",
    )
