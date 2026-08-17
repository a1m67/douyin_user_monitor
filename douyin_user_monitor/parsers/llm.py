"""Conservative LLM fallback for unresolved episode metadata."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

import httpx

from douyin_user_monitor.parsers.base import (
    CONTENT_TYPES,
    MATCHED,
    REVIEW,
    EpisodeParseInput,
    EpisodeParseResult,
)
from douyin_user_monitor.parsers.regex import normalize_title


class LLMClient(Protocol):
    def complete(self, input_payload: Mapping[str, Any]) -> str:
        ...


class LLMTimeoutError(RuntimeError):
    pass


class LLMHTTPError(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMDecision:
    is_episode: bool
    show_title: str | None
    show_id: int | None
    episode_number: int | None
    content_type: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_episode": self.is_episode,
            "show_title": self.show_title,
            "show_id": self.show_id,
            "episode_number": self.episode_number,
            "content_type": self.content_type,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class OpenAICompatibleLLMClient:
    """Minimal Chat Completions client without provider-specific dependencies."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("LLM_API_KEY 不能为空")
        if not base_url.strip():
            raise ValueError("LLM_BASE_URL 不能为空")
        if not model.strip():
            raise ValueError("LLM_MODEL 不能为空")
        self._api_key = api_key.strip()
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model.strip()
        self._timeout_seconds = float(timeout_seconds)

    def complete(self, input_payload: Mapping[str, Any]) -> str:
        system_prompt = (
            "你是短剧作品分类器。只输出一个严格 JSON 对象，不要 Markdown。"
            "不得把先导片、预告片推断为第0集；只有标题明确出现第0集、EP0 或 Episode 0 才可返回0。"
            "优先匹配 known_shows，无法确定时降低 confidence。"
        )
        schema_prompt = {
            "required_output": {
                "is_episode": "boolean",
                "show_title": "string|null",
                "show_id": "integer|null",
                "episode_number": "integer|null (>=0)",
                "content_type": sorted(CONTENT_TYPES),
                "confidence": "number 0..1",
                "reason": "non-empty string",
            },
            "input": input_payload,
        }
        try:
            response = httpx.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(schema_prompt, ensure_ascii=False),
                        },
                    ],
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("LLM request timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMHTTPError("LLM HTTP request failed") from exc
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError("LLM response envelope is invalid") from exc
        if not isinstance(content, str):
            raise LLMResponseError("LLM message content must be a string")
        return content


class LLMParser:
    def __init__(
        self,
        client: LLMClient,
        *,
        auto_accept_confidence: float = 0.90,
        max_known_shows: int = 8,
        recent_video_limit: int = 5,
    ) -> None:
        if not 0 <= auto_accept_confidence <= 1:
            raise ValueError("LLM_AUTO_ACCEPT_CONFIDENCE 必须在 0 到 1 之间")
        self._client = client
        self._auto_accept_confidence = auto_accept_confidence
        self._max_known_shows = max(1, max_known_shows)
        self._recent_video_limit = max(1, recent_video_limit)

    def parse(
        self,
        request: EpisodeParseInput,
        regex_result: EpisodeParseResult,
    ) -> EpisodeParseResult:
        candidates = _relevant_known_shows(
            request,
            regex_result,
            limit=self._max_known_shows,
        )
        input_payload = _llm_input(
            request,
            regex_result,
            candidates,
            recent_video_limit=self._recent_video_limit,
        )
        try:
            raw = self._client.complete(input_payload)
            decision = _validate_decision(raw)
        except LLMTimeoutError:
            return _failed_result(regex_result, "llm_timeout")
        except LLMHTTPError:
            return _failed_result(regex_result, "llm_http_error")
        except (LLMResponseError, ValueError, TypeError, json.JSONDecodeError):
            return _failed_result(regex_result, "llm_invalid_response")
        except Exception:
            # A model integration must never abort account inspection.
            return _failed_result(regex_result, "llm_call_failed")

        decision_dict = decision.to_dict()
        matched_show = _match_known_show(decision, candidates)
        can_auto_accept = (
            decision.content_type == "episode"
            and decision.is_episode
            and decision.confidence >= self._auto_accept_confidence
            and decision.episode_number is not None
            and bool(decision.show_title)
            and matched_show is not None
        )
        canonical_title = (
            str(matched_show["title"]).strip()
            if matched_show is not None
            else decision.show_title
        )
        return EpisodeParseResult(
            status=MATCHED if can_auto_accept else REVIEW,
            show_title=canonical_title,
            episode_number=decision.episode_number,
            confidence=decision.confidence,
            reason=decision.reason,
            method="llm",
            matched_show_id=int(matched_show["id"]) if matched_show is not None else None,
            show_title_candidate=decision.show_title or regex_result.show_title_candidate,
            episode_candidate=(
                decision.episode_number
                if decision.episode_number is not None
                else regex_result.episode_candidate
            ),
            content_type=decision.content_type,
            episode_evidence=regex_result.episode_evidence,
            show_evidence=regex_result.show_evidence,
            regex_result=_compact_parse_result(regex_result),
            llm_result=decision_dict,
            llm_raw_result=decision_dict,
        )


def _llm_input(
    request: EpisodeParseInput,
    regex_result: EpisodeParseResult,
    known_shows: Sequence[dict[str, Any]],
    *,
    recent_video_limit: int,
) -> dict[str, Any]:
    return {
        "display_title": request.display_title,
        "description": request.description,
        "hashtags": list(request.hashtags),
        "account_nickname": request.account_nickname,
        "regex_result": _compact_parse_result(regex_result),
        "known_shows": [
            {
                "id": show.get("id"),
                "title": show.get("title"),
                "aliases": list(show.get("aliases") or []),
            }
            for show in known_shows
        ],
        "recent_account_videos": [
            {
                "display_title": video.get("display_title"),
                "description": video.get("description"),
                "parsed_show_title": video.get("parsed_show_title"),
                "parsed_episode_number": video.get("parsed_episode_number"),
                "content_type": video.get("content_type"),
            }
            for video in request.recent_account_videos[:recent_video_limit]
        ],
    }


def _compact_parse_result(result: EpisodeParseResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "show_title": result.show_title,
        "episode_number": result.episode_number,
        "show_title_candidate": result.show_title_candidate,
        "episode_candidate": result.episode_candidate,
        "content_type": result.content_type,
        "confidence": result.confidence,
        "reason": result.reason,
        "method": result.method,
        "matched_show_id": result.matched_show_id,
    }


def _validate_decision(raw: str) -> LLMDecision:
    if not isinstance(raw, str) or not raw.strip():
        raise LLMResponseError("LLM output is empty")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise LLMResponseError("LLM output must be an object")
    required = {
        "is_episode",
        "show_title",
        "show_id",
        "episode_number",
        "content_type",
        "confidence",
        "reason",
    }
    if set(value) != required:
        raise LLMResponseError("LLM output fields do not match the schema")
    if type(value["is_episode"]) is not bool:
        raise LLMResponseError("is_episode must be boolean")
    show_title = value["show_title"]
    if show_title is not None and (not isinstance(show_title, str) or not show_title.strip()):
        raise LLMResponseError("show_title must be a non-empty string or null")
    show_id = value["show_id"]
    if show_id is not None and (type(show_id) is not int or show_id <= 0):
        raise LLMResponseError("show_id must be a positive integer or null")
    episode_number = value["episode_number"]
    if episode_number is not None and (type(episode_number) is not int or episode_number < 0):
        raise LLMResponseError("episode_number must be a non-negative integer or null")
    content_type = value["content_type"]
    if content_type not in CONTENT_TYPES:
        raise LLMResponseError("content_type is invalid")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise LLMResponseError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1:
        raise LLMResponseError("confidence must be between 0 and 1")
    reason = value["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise LLMResponseError("reason must be a non-empty string")
    if content_type in {"trailer", "show_content", "non_drama", "unknown"} and episode_number is not None:
        raise LLMResponseError("non-episode content cannot have an episode number")
    if content_type == "episode" and not value["is_episode"]:
        raise LLMResponseError("episode content must set is_episode=true")
    return LLMDecision(
        is_episode=value["is_episode"],
        show_title=show_title.strip() if isinstance(show_title, str) else None,
        show_id=show_id,
        episode_number=episode_number,
        content_type=content_type,
        confidence=float(confidence),
        reason=reason.strip(),
    )


def _relevant_known_shows(
    request: EpisodeParseInput,
    regex_result: EpisodeParseResult,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    text = normalize_title(
        " ".join(
            [
                request.display_title,
                request.description,
                *request.hashtags,
                *request.text_sources.values(),
                regex_result.show_title or "",
                regex_result.show_title_candidate or "",
            ]
        )
    )
    account_ids = {
        int(show["id"])
        for show in request.account_show_candidates
        if isinstance(show.get("id"), int)
    }
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, show in enumerate(request.known_shows):
        title = str(show.get("title") or "").strip()
        if not title:
            continue
        names = [title, *(show.get("aliases") or [])]
        normalized_names = [normalize_title(str(name)) for name in names]
        score = 0
        if show.get("id") == regex_result.matched_show_id:
            score += 1000
        if show.get("id") in account_ids:
            score += 200
        score += max((len(name) for name in normalized_names if name and name in text), default=0) * 20
        scored.append((score, -index, dict(show)))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    relevant = [item[2] for item in scored if item[0] > 0]
    if not relevant:
        relevant = [item[2] for item in scored[:limit]]
    return relevant[:limit]


def _match_known_show(
    decision: LLMDecision,
    known_shows: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not decision.show_title:
        return None
    normalized = normalize_title(decision.show_title)
    matches = []
    for show in known_shows:
        names = [str(show.get("title") or ""), *(show.get("aliases") or [])]
        if any(normalized == normalize_title(str(name)) for name in names):
            matches.append(show)
    if len(matches) != 1:
        return None
    match = matches[0]
    if decision.show_id is not None and int(match.get("id") or 0) != decision.show_id:
        return None
    return match


def _failed_result(base: EpisodeParseResult, reason: str) -> EpisodeParseResult:
    return EpisodeParseResult(
        status=REVIEW,
        show_title=base.show_title,
        episode_number=base.episode_number,
        confidence=base.confidence,
        reason=reason,
        method="llm",
        matched_show_id=base.matched_show_id,
        show_title_candidate=base.show_title_candidate,
        episode_candidate=base.episode_candidate,
        content_type=base.content_type,
        episode_evidence=base.episode_evidence,
        show_evidence=base.show_evidence,
        regex_result=_compact_parse_result(base),
        llm_result={"error": reason},
    )
