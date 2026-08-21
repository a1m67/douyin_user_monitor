from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from douyin_user_monitor.repositories.sqlite import SCHEMA_VERSION, ShortDramaRepository
from douyin_user_monitor.services.media_cache import (
    MediaCacheConfig,
    MediaCacheService,
    MediaFetchError,
)


async def public_resolver(_: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


class MediaCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repository = ShortDramaRepository(self.root / "app.db")
        self.calls = 0

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def service(self, handler, **changes) -> MediaCacheService:
        values = {
            "max_bytes": 1024,
            "ttl_hours": 24,
            "max_file_bytes": 32,
        }
        values.update(changes)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return MediaCacheService(
            self.repository,
            self.root / "media-cache",
            MediaCacheConfig(**values),
            client=client,
            resolver=public_resolver,
        )

    async def test_image_is_cached_and_second_request_does_not_hit_upstream(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"image")

        service = self.service(handler)
        first = await service.get("https://cdn.example/avatar.jpg")
        second = await service.get("https://cdn.example/avatar.jpg")
        self.assertEqual(first.content, b"image")
        self.assertEqual(second.content, b"image")
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.repository.list_media_cache_entries_lru()), 1)
        await service.aclose()

    async def test_expired_entry_refreshes_and_stale_is_used_on_error(self):
        payloads = [b"first", b"second"]

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if payloads:
                return httpx.Response(200, headers={"content-type": "image/png"}, content=payloads.pop(0))
            raise httpx.ConnectError("offline", request=request)

        service = self.service(handler, ttl_hours=1)
        url = "https://cdn.example/cover.png"
        self.assertEqual((await service.get(url)).content, b"first")
        cache_key = hashlib.sha256(url.encode()).hexdigest()
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
        connection = sqlite3.connect(self.repository.database_path)
        try:
            connection.execute(
                "UPDATE media_cache_entries SET fetched_at=? WHERE cache_key=?", (old, cache_key)
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual((await service.get(url)).content, b"second")
        connection = sqlite3.connect(self.repository.database_path)
        try:
            connection.execute(
                "UPDATE media_cache_entries SET fetched_at=? WHERE cache_key=?", (old, cache_key)
            )
            connection.commit()
        finally:
            connection.close()
        stale = await service.get(url)
        self.assertEqual(stale.content, b"second")
        self.assertTrue(stale.stale)
        await service.aclose()

    async def test_private_addresses_and_redirects_are_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"})

        service = self.service(handler)
        with self.assertRaises(MediaFetchError):
            await service.fetch_remote("http://127.0.0.1/avatar.jpg")
        with self.assertRaises(MediaFetchError):
            await service.fetch_remote("https://cdn.example/avatar.jpg")
        placeholder = await service.get("http://10.0.0.2/private.jpg")
        self.assertTrue(placeholder.placeholder)
        self.assertEqual(self.repository.list_media_cache_entries_lru(), [])
        await service.aclose()

    async def test_wrong_type_oversize_and_video_are_never_cached(self):
        responses = {
            "/html": ("text/html", b"<html>"),
            "/large": ("image/jpeg", b"x" * 33),
            "/video": ("video/mp4", b"video"),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            content_type, content = responses[request.url.path]
            return httpx.Response(200, headers={"content-type": content_type}, content=content)

        service = self.service(handler, max_file_bytes=32)
        for path in responses:
            with self.assertRaises(MediaFetchError):
                await service.fetch_remote(f"https://cdn.example{path}")
            self.assertTrue((await service.get(f"https://cdn.example{path}")).placeholder)
        self.assertEqual(self.repository.list_media_cache_entries_lru(), [])
        await service.aclose()

    async def test_lru_eviction_removes_oldest_files_until_under_limit(self):
        cache_dir = self.root / "media-cache"
        cache_dir.mkdir()
        service = MediaCacheService(
            self.repository,
            cache_dir,
            MediaCacheConfig(max_bytes=5),
            client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))),
            resolver=public_resolver,
        )
        for key in ("old", "new"):
            (cache_dir / f"{key}.bin").write_bytes(b"1234")
            self.repository.upsert_media_cache_entry(
                cache_key=key,
                url=f"https://cdn.example/{key}",
                relative_path=f"{key}.bin",
                content_type="image/jpeg",
                size_bytes=4,
            )
        connection = sqlite3.connect(self.repository.database_path)
        try:
            connection.execute(
                "UPDATE media_cache_entries SET last_access_at=? WHERE cache_key='old'",
                ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),),
            )
            connection.commit()
        finally:
            connection.close()
        result = service.evict_lru()
        self.assertEqual(result["removed"], 1)
        self.assertFalse((cache_dir / "old.bin").exists())
        self.assertTrue((cache_dir / "new.bin").exists())
        self.assertEqual(self.repository.schema_version(), SCHEMA_VERSION)
        await service.aclose()


if __name__ == "__main__":
    unittest.main()
