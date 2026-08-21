"""Bounded, SSRF-resistant cache for remote avatars and cover images."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from douyin_user_monitor.repositories.sqlite import ShortDramaRepository


PLACEHOLDER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 210"><rect width="160" height="210" fill="#e8ecef"/><path d="M28 154l34-42 24 27 18-20 28 35H28z" fill="#aab4bc"/><circle cx="58" cy="72" r="18" fill="#aab4bc"/></svg>"""
Resolver = Callable[[str], Awaitable[Sequence[str]]]


class MediaFetchError(RuntimeError):
    """Remote media failed validation or download."""


@dataclass(frozen=True)
class MediaCacheConfig:
    enabled: bool = True
    max_bytes: int = 512 * 1024 * 1024
    ttl_hours: int = 168
    timeout_seconds: float = 10.0
    max_file_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 3


@dataclass(frozen=True)
class CachedMedia:
    content: bytes
    content_type: str
    stale: bool = False
    placeholder: bool = False


class MediaCacheService:
    def __init__(
        self,
        repository: ShortDramaRepository,
        cache_dir: Path,
        config: MediaCacheConfig,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._repository = repository
        self._cache_dir = Path(cache_dir).resolve()
        self._config = config
        self._client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None
        self._resolver = resolver or _resolve_host
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(self, url: str | None) -> CachedMedia:
        if not self._config.enabled or not str(url or "").strip():
            return self.placeholder()
        normalized_url = str(url).strip()
        cache_key = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = await asyncio.to_thread(self._read_cached, cache_key)
            if cached is not None and not self._is_expired(cached[1]):
                await asyncio.to_thread(self._repository.touch_media_cache_entry, cache_key)
                return cached[0]
            try:
                downloaded = await self.fetch_remote(normalized_url)
                await asyncio.to_thread(self._store, cache_key, normalized_url, downloaded)
                return downloaded
            except (MediaFetchError, httpx.HTTPError, OSError):
                if cached is not None:
                    await asyncio.to_thread(self._repository.touch_media_cache_entry, cache_key)
                    return CachedMedia(cached[0].content, cached[0].content_type, stale=True)
                return self.placeholder()
            finally:
                self._locks.pop(cache_key, None)

    async def fetch_remote(self, url: str) -> CachedMedia:
        current = url
        for redirect_count in range(self._config.max_redirects + 1):
            await self._validate_url(current)
            request = self._client.build_request("GET", current, headers={"Accept": "image/*"})
            response = await self._client.send(request, stream=True)
            try:
                if response.is_redirect:
                    if redirect_count >= self._config.max_redirects:
                        raise MediaFetchError("remote image exceeded redirect limit")
                    location = response.headers.get("location")
                    if not location:
                        raise MediaFetchError("remote image redirect omitted location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/"):
                    raise MediaFetchError("remote media is not an image")
                length = response.headers.get("content-length")
                if length:
                    try:
                        declared_size = int(length)
                    except ValueError as exc:
                        raise MediaFetchError("remote image content length is invalid") from exc
                    if declared_size > self._config.max_file_bytes:
                        raise MediaFetchError("remote image is too large")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self._config.max_file_bytes:
                        raise MediaFetchError("remote image is too large")
                    chunks.append(chunk)
                return CachedMedia(b"".join(chunks), content_type)
            finally:
                await response.aclose()
        raise MediaFetchError("remote image exceeded redirect limit")

    async def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaFetchError("remote image URL must use HTTP(S)")
        if parsed.username or parsed.password:
            raise MediaFetchError("remote image URL credentials are not allowed")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
            addresses = [str(literal)]
        except ValueError:
            addresses = list(await self._resolver(parsed.hostname))
        if not addresses:
            raise MediaFetchError("remote image host did not resolve")
        for address in addresses:
            try:
                parsed_address = ipaddress.ip_address(address)
            except ValueError as exc:
                raise MediaFetchError("remote image host resolved unexpectedly") from exc
            if not parsed_address.is_global:
                raise MediaFetchError("remote image host resolved to a non-public address")

    def evict_lru(self) -> dict[str, int]:
        entries = self._repository.list_media_cache_entries_lru()
        total = sum(int(entry["size_bytes"]) for entry in entries)
        removed = 0
        removed_bytes = 0
        for entry in entries:
            path = self._cache_dir / str(entry["relative_path"])
            size = int(entry["size_bytes"])
            if path.is_file() and total <= self._config.max_bytes:
                continue
            path.unlink(missing_ok=True)
            self._repository.delete_media_cache_entry(str(entry["cache_key"]))
            total -= size
            removed += 1
            removed_bytes += size
        return {"removed": removed, "removed_bytes": removed_bytes, "size_bytes": max(0, total)}

    @staticmethod
    def placeholder() -> CachedMedia:
        return CachedMedia(PLACEHOLDER_SVG, "image/svg+xml", placeholder=True)

    def _read_cached(self, cache_key: str) -> tuple[CachedMedia, str] | None:
        entry = self._repository.get_media_cache_entry(cache_key)
        if entry is None:
            return None
        path = self._cache_dir / str(entry["relative_path"])
        try:
            content = path.read_bytes()
        except OSError:
            self._repository.delete_media_cache_entry(cache_key)
            return None
        return CachedMedia(content, str(entry["content_type"])), str(entry["fetched_at"])

    def _store(self, cache_key: str, url: str, media: CachedMedia) -> None:
        relative_path = f"{cache_key}.bin"
        target = self._cache_dir / relative_path
        temp = self._cache_dir / f".{cache_key}.tmp"
        with temp.open("wb") as handle:
            handle.write(media.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        self._repository.upsert_media_cache_entry(
            cache_key=cache_key,
            url=url,
            relative_path=relative_path,
            content_type=media.content_type,
            size_bytes=len(media.content),
        )

    def _is_expired(self, fetched_at: str) -> bool:
        try:
            fetched = datetime.fromisoformat(fetched_at)
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched >= timedelta(hours=self._config.ttl_hours)


async def _resolve_host(hostname: str) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))
