"""Safe normalization of the account inputs users copy from Douyin."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx


_DOUYIN_HOSTS = frozenset({"douyin.com", "www.douyin.com", "v.douyin.com", "m.douyin.com"})
_SHORT_HOSTS = frozenset({"v.douyin.com"})
_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SEC_UID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
_PROFILE_PATH_PATTERNS = (
    re.compile(r"^/user/([^/?#]+)", re.IGNORECASE),
    re.compile(r"^/share/user/([^/?#]+)", re.IGNORECASE),
)
_VIDEO_PATH_PATTERN = re.compile(r"^/(?:video|note)/([0-9]+)", re.IGNORECASE)


class AccountInputCrawler(Protocol):
    async def fetch_one_video(self, aweme_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ResolvedDouyinAccountInput:
    sec_uid: str
    canonical_homepage_url: str
    input_type: str
    resolved_url: str
    aweme_id: str | None = None


class AccountInputResolver:
    """Resolve profile, short, video, share-text, and advanced sec_uid inputs."""

    def __init__(
        self,
        crawler: AccountInputCrawler,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 8.0,
        max_redirects: int = 5,
    ) -> None:
        self._crawler = crawler
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._max_redirects = max_redirects

    async def resolve(self, raw_input: str) -> ResolvedDouyinAccountInput:
        value = str(raw_input or "").strip()
        if not value:
            raise ValueError("请粘贴抖音作者主页、分享短链或作品链接")

        if _SEC_UID_PATTERN.fullmatch(value) and "://" not in value:
            return self._profile_result(value, "sec_uid", self._canonical_url(value))

        initial_url = self._extract_douyin_url(value)
        initial = self._validated_url(initial_url)
        was_short = initial.hostname in _SHORT_HOSTS
        resolved_url = await self._follow_short_link(initial_url) if was_short else initial_url
        resolved = self._validated_url(resolved_url)

        sec_uid = self._profile_sec_uid(resolved)
        if sec_uid:
            return self._profile_result(
                sec_uid,
                "profile_short_url" if was_short else "profile_url",
                resolved_url,
            )

        video_match = _VIDEO_PATH_PATTERN.match(resolved.path)
        if video_match:
            aweme_id = video_match.group(1)
            sec_uid = await self._author_sec_uid(aweme_id)
            return ResolvedDouyinAccountInput(
                sec_uid=sec_uid,
                canonical_homepage_url=self._canonical_url(sec_uid),
                input_type="video_short_url" if was_short else "video_url",
                resolved_url=resolved_url,
                aweme_id=aweme_id,
            )

        raise ValueError("无法识别该抖音分享链接，请粘贴作者主页或任意作品链接")

    def _extract_douyin_url(self, value: str) -> str:
        matches = _URL_PATTERN.findall(value)
        if not matches:
            raise ValueError("无法识别该抖音分享内容，请确认其中包含抖音链接")
        for match in matches:
            candidate = match.rstrip(".,;:!?，。；：！？、)]}）】》")
            try:
                self._validated_url(candidate)
            except ValueError:
                continue
            return candidate
        raise ValueError("只支持抖音官方链接")

    def _validated_url(self, value: str):
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise ValueError("只支持安全的抖音 http/https 链接")
        host = (parsed.hostname or "").rstrip(".").casefold()
        if host not in _DOUYIN_HOSTS:
            raise ValueError("只支持抖音官方链接")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("抖音链接端口无效") from exc
        if port is not None and port not in {80, 443}:
            raise ValueError("抖音链接端口无效")
        if parsed.username or parsed.password:
            raise ValueError("抖音链接不能包含用户凭据")
        return parsed

    async def _follow_short_link(self, initial_url: str) -> str:
        current = initial_url
        visited: set[str] = set()
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"},
            ) as client:
                for _ in range(self._max_redirects + 1):
                    self._validated_url(current)
                    if current in visited:
                        raise ValueError("抖音短链重定向循环，无法完成解析")
                    visited.add(current)
                    response = await client.get(current)
                    if response.is_redirect:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise ValueError("抖音短链返回了无效跳转")
                        target = urljoin(current, location)
                        self._validated_url(target)
                        current = target
                        continue
                    if response.status_code >= 400:
                        raise ValueError(f"抖音短链解析失败（HTTP {response.status_code}）")
                    return str(response.url)
        except httpx.TimeoutException as exc:
            raise ValueError("抖音短链解析超时，请稍后重试") from exc
        except httpx.RequestError as exc:
            raise ValueError("抖音短链暂时无法访问，请稍后重试") from exc
        raise ValueError("抖音短链跳转次数过多，无法完成解析")

    def _profile_sec_uid(self, parsed: Any) -> str | None:
        for pattern in _PROFILE_PATH_PATTERNS:
            match = pattern.match(parsed.path)
            if match:
                return match.group(1).strip()
        values = parse_qs(parsed.query).get("sec_uid", [])
        return str(values[0]).strip() if values and str(values[0]).strip() else None

    async def _author_sec_uid(self, aweme_id: str) -> str:
        payload = await self._crawler.fetch_one_video(aweme_id)
        for item in _video_payload_candidates(payload):
            author = item.get("author")
            if not isinstance(author, Mapping):
                continue
            for field in ("sec_uid", "sec_user_id"):
                value = str(author.get(field) or "").strip()
                if value:
                    return value
        raise ValueError("已识别作品链接，但无法读取作者信息，请稍后重试")

    @staticmethod
    def _canonical_url(sec_uid: str) -> str:
        return f"https://www.douyin.com/user/{sec_uid}"

    def _profile_result(
        self, sec_uid: str, input_type: str, resolved_url: str
    ) -> ResolvedDouyinAccountInput:
        return ResolvedDouyinAccountInput(
            sec_uid=sec_uid,
            canonical_homepage_url=self._canonical_url(sec_uid),
            input_type=input_type,
            resolved_url=resolved_url,
        )


def _video_payload_candidates(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = [payload]
    for key in ("aweme_detail", "aweme_info", "item"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            candidates.append(value)
    for key in ("item_list", "aweme_list"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, Mapping))
    return candidates
