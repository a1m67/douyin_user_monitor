from __future__ import annotations

import unittest

from douyin_user_monitor.providers.base import ProviderAccount
from douyin_user_monitor.providers.builtin_douyin import BuiltinDouyinProvider


class StubCrawler:
    async def get_sec_user_id(self, url: str) -> str:
        self.resolved_url = url
        return "sec-1"

    async def handler_user_profile(self, sec_user_id: str):
        self.profile_sec_uid = sec_user_id
        return {
            "user": {
                "nickname": "AI 剧场",
                "avatar_thumb": {"url_list": ["https://cover.example/avatar.jpg"]},
            }
        }

    async def fetch_user_post_videos(self, sec_user_id: str, max_cursor: int, count: int):
        self.video_args = (sec_user_id, max_cursor, count)
        return {
            "aweme_list": [
                {
                    "aweme_id": "1002",
                    "desc": "原创ai漫剧《契鬼人》义庄副本第一夜 #短剧",
                    "item_title": "原创ai漫剧《契鬼人》义庄副本第一夜",
                    "series_play_info": {"item_title_prefix": {"text": "第8集"}},
                    "create_time": 1_700_000_000,
                    "text_extra": [{"hashtag_name": "末日重生"}],
                    "video": {"cover": {"url_list": ["https://cover.example/12.jpg"]}},
                },
                {
                    "aweme_id": "1001",
                    "desc": "older",
                    "create_time": 1_600_000_000,
                },
            ]
        }

    async def fetch_one_video(self, aweme_id: str):
        raise AssertionError(f"provider should not fetch video detail: {aweme_id}")

    async def get_douyin_headers(self):
        return {"headers": {}}

    async def aclose(self) -> None:
        self.closed = True


class BuiltinDouyinProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.crawler = StubCrawler()
        self.provider = BuiltinDouyinProvider(self.crawler)

    async def test_resolve_profile_and_latest_videos_without_exposing_crawler_shape(self):
        account = await self.provider.resolve_account("https://www.douyin.com/user/example")
        self.assertEqual(account.sec_uid, "example")
        self.assertEqual(account.homepage_url, "https://www.douyin.com/user/example")

        profile = await self.provider.get_user_profile(account)
        self.assertEqual(profile.nickname, "AI 剧场")
        self.assertEqual(profile.avatar_url, "https://cover.example/avatar.jpg")

        videos = await self.provider.get_latest_videos(account, limit=10)
        self.assertEqual(self.crawler.video_args, ("example", 0, 10))
        self.assertEqual([video.aweme_id for video in videos], ["1002", "1001"])
        self.assertEqual(videos[0].hashtags, ("末日重生",))
        self.assertEqual(videos[0].cover_url, "https://cover.example/12.jpg")
        self.assertEqual(videos[0].video_url, "https://www.douyin.com/video/1002")
        self.assertEqual(videos[0].description, "原创ai漫剧《契鬼人》义庄副本第一夜 #短剧")
        self.assertEqual(videos[0].display_title, "第8集 | 原创ai漫剧《契鬼人》义庄副本第一夜")
        self.assertEqual(videos[0].text_sources["series_play_info.item_title_prefix.text"], "第8集")
        self.assertEqual(videos[0].text_sources["item_title"], "原创ai漫剧《契鬼人》义庄副本第一夜")

    async def test_rejects_non_positive_limit(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            await self.provider.get_latest_videos(ProviderAccount(id="1", sec_uid="sec-1"), 0)

    async def test_video_page_preserves_cursor_and_has_more(self):
        account = ProviderAccount(id="1", sec_uid="sec-1")
        page = await self.provider.get_video_page(account, cursor=123, limit=5)

        self.assertEqual(self.crawler.video_args, ("sec-1", 123, 5))
        self.assertEqual([video.aweme_id for video in page.videos], ["1002", "1001"])
        self.assertEqual(page.next_cursor, 123)
        self.assertFalse(page.has_more)


if __name__ == "__main__":
    unittest.main()
