from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from douyin_user_monitor.providers.account_input import AccountInputResolver
from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline


SEC_UID = "MS4wLjABAAAAexample-sec-uid"


class StubCrawler:
    def __init__(self, payload=None):
        self.payload = payload or {"aweme_detail": {"author": {"sec_uid": SEC_UID}}}
        self.aweme_ids: list[str] = []

    async def fetch_one_video(self, aweme_id: str):
        self.aweme_ids.append(aweme_id)
        return self.payload


def redirect_transport(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        response = routes.get(str(request.url))
        if isinstance(response, Exception):
            raise response
        if response is None:
            return httpx.Response(404, request=request)
        status, headers = response
        return httpx.Response(status, headers=headers, request=request)

    return httpx.MockTransport(handler)


class AccountInputResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_profile_url_is_resolved_without_http(self):
        resolver = AccountInputResolver(StubCrawler())
        result = await resolver.resolve(f"https://www.douyin.com/user/{SEC_UID}?from_tab_name=main")
        self.assertEqual(result.sec_uid, SEC_UID)
        self.assertEqual(result.input_type, "profile_url")
        self.assertEqual(result.canonical_homepage_url, f"https://www.douyin.com/user/{SEC_UID}")

    async def test_short_profile_redirect_and_share_text(self):
        short = "https://v.douyin.com/abc123/"
        target = f"https://www.douyin.com/user/{SEC_UID}?from_tab_name=main"
        resolver = AccountInputResolver(
            StubCrawler(), transport=redirect_transport({short: (302, {"location": target}), target: (200, {})})
        )
        result = await resolver.resolve(f"复制此链接，打开抖音！\n{short}\n8@7.com :8pm")
        self.assertEqual(result.sec_uid, SEC_UID)
        self.assertEqual(result.input_type, "profile_short_url")

    async def test_short_redirect_query_sec_uid(self):
        short = "https://v.douyin.com/query/"
        target = f"https://m.douyin.com/share/user/anything?sec_uid={SEC_UID}"
        resolver = AccountInputResolver(
            StubCrawler(), transport=redirect_transport({short: (302, {"location": target}), target: (200, {})})
        )
        result = await resolver.resolve(short)
        self.assertEqual(result.sec_uid, "anything")

    async def test_short_redirect_query_sec_uid_without_profile_path(self):
        short = "https://v.douyin.com/query2/"
        target = f"https://www.douyin.com/?sec_uid={SEC_UID}"
        resolver = AccountInputResolver(
            StubCrawler(), transport=redirect_transport({short: (302, {"location": target}), target: (200, {})})
        )
        result = await resolver.resolve(short)
        self.assertEqual(result.sec_uid, SEC_UID)

    async def test_video_url_fetches_author(self):
        crawler = StubCrawler({"item_list": [{"author": {"sec_user_id": SEC_UID}}]})
        result = await AccountInputResolver(crawler).resolve("https://www.douyin.com/video/123456")
        self.assertEqual(result.sec_uid, SEC_UID)
        self.assertEqual(result.aweme_id, "123456")
        self.assertEqual(result.input_type, "video_url")
        self.assertEqual(crawler.aweme_ids, ["123456"])

    async def test_video_short_link_fetches_author(self):
        short = "https://v.douyin.com/video-share/"
        target = "https://www.douyin.com/note/998877"
        crawler = StubCrawler()
        resolver = AccountInputResolver(
            crawler, transport=redirect_transport({short: (302, {"location": target}), target: (200, {})})
        )
        result = await resolver.resolve(short)
        self.assertEqual(result.input_type, "video_short_url")
        self.assertEqual(result.aweme_id, "998877")
        self.assertEqual(crawler.aweme_ids, ["998877"])

    async def test_bare_sec_uid(self):
        result = await AccountInputResolver(StubCrawler()).resolve(SEC_UID)
        self.assertEqual(result.input_type, "sec_uid")
        self.assertEqual(result.sec_uid, SEC_UID)

    async def test_rejects_non_douyin_and_private_redirect(self):
        resolver = AccountInputResolver(StubCrawler())
        with self.assertRaisesRegex(ValueError, "抖音"):
            await resolver.resolve("https://example.com/user/test")

        short = "https://v.douyin.com/unsafe/"
        resolver = AccountInputResolver(
            StubCrawler(),
            transport=redirect_transport({short: (302, {"location": "http://127.0.0.1/admin"})}),
        )
        with self.assertRaisesRegex(ValueError, "抖音官方"):
            await resolver.resolve(short)

    async def test_redirect_loop_and_timeout_are_readable(self):
        first = "https://v.douyin.com/loop/"
        second = "https://www.douyin.com/loop/"
        resolver = AccountInputResolver(
            StubCrawler(),
            transport=redirect_transport(
                {first: (302, {"location": second}), second: (302, {"location": first})}
            ),
        )
        with self.assertRaisesRegex(ValueError, "循环"):
            await resolver.resolve(first)

        timeout_resolver = AccountInputResolver(
            StubCrawler(),
            transport=redirect_transport({first: httpx.ReadTimeout("late")}),
        )
        with self.assertRaisesRegex(ValueError, "超时"):
            await timeout_resolver.resolve(first)


class SameAccountProvider:
    async def resolve_account(self, raw_input: str):
        return ProviderAccount(
            id="", sec_uid=SEC_UID, homepage_url=f"https://www.douyin.com/user/{SEC_UID}"
        )

    async def get_user_profile(self, account):
        return ProviderProfile(nickname="同一作者")


class AccountInputDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_different_inputs_create_only_one_account(self):
        with TemporaryDirectory() as directory:
            repository = ShortDramaRepository(Path(directory) / "app.db")
            pipeline = ShortDramaPipeline(repository=repository, provider=SameAccountProvider())

            first, first_created = await pipeline.add_account(
                f"https://www.douyin.com/user/{SEC_UID}"
            )
            second, second_created = await pipeline.add_account("https://v.douyin.com/example/")

            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(repository.list_accounts()), 1)


if __name__ == "__main__":
    unittest.main()
