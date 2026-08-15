from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile, ProviderVideo
from douyin_user_monitor.providers.fake import FakeDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline


def make_video(aweme_id: str, description: str, timestamp: int) -> ProviderVideo:
    return ProviderVideo(
        aweme_id=aweme_id,
        description=description,
        hashtags=(),
        publish_time=f"2026-08-15T12:{timestamp:02d}:00+00:00",
        video_url=f"https://www.douyin.com/video/{aweme_id}",
        cover_url="https://cover.example/cover.jpg",
        raw={"aweme_id": aweme_id, "desc": description},
    )


class ShortDramaPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.repository = ShortDramaRepository(root / "app.db")
        self.account_one_provider = ProviderAccount(
            id="",
            sec_uid="sec-one",
            homepage_url="https://www.douyin.com/user/one",
        )
        self.account_two_provider = ProviderAccount(
            id="",
            sec_uid="sec-two",
            homepage_url="https://www.douyin.com/user/two",
        )
        self.provider = FakeDouyinProvider(
            accounts_by_url={
                self.account_one_provider.homepage_url: self.account_one_provider,
                self.account_two_provider.homepage_url: self.account_two_provider,
            },
            profiles_by_sec_uid={
                "sec-one": ProviderProfile(nickname="AI末日剧场"),
                "sec-two": ProviderProfile(nickname="转载剧场"),
            },
            videos_by_sec_uid={
                "sec-one": [
                    make_video("1003", "《末日重生》第16集", 3),
                    make_video("1002", "《末日重生》第15集", 2),
                    make_video("1001", "《末日重生》第14集", 1),
                ],
                "sec-two": [make_video("2001", "《末日重生》第16集", 4)],
            },
        )
        self.pipeline = ShortDramaPipeline(
            repository=self.repository,
            provider=self.provider,
            initial_sync_limit=20,
            notify_on_initial_sync=False,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def add_first_account_and_sync_baseline(self) -> dict:
        account, created = await self.pipeline.add_account(self.account_one_provider.homepage_url)
        self.assertTrue(created)
        result = await self.pipeline.sync_account(account["id"])
        self.assertTrue(result.initial_sync)
        self.assertEqual(result.new_videos, 3)
        self.assertEqual(result.new_episode_updates, ())
        return account

    async def test_acceptance_scenarios_a_to_d(self):
        first_account = await self.add_first_account_and_sync_baseline()
        shows = self.repository.list_shows()
        self.assertEqual(len(shows), 1)
        show = shows[0]
        self.assertEqual(show["title"], "末日重生")
        self.assertEqual(show["latest_episode"], 16)
        self.assertEqual([episode["episode_number"] for episode in self.repository.get_show_episodes(show["id"])], [16, 15, 14])

        same_result = await self.pipeline.sync_account(first_account["id"])
        self.assertEqual(same_result.new_videos, 0)
        self.assertEqual(same_result.duplicate_videos, 3)
        self.assertEqual(same_result.new_episode_updates, ())

        second_account, created = await self.pipeline.add_account(self.account_two_provider.homepage_url)
        self.assertTrue(created)
        second_result = await self.pipeline.sync_account(second_account["id"])
        self.assertEqual(second_result.new_videos, 1)
        self.assertEqual(len(self.repository.get_show_episodes(show["id"])), 3)
        episode_sixteen = self.repository.get_show_episodes(show["id"])[0]
        self.assertEqual(len(self.repository.get_episode_sources(episode_sixteen["id"])), 2)
        self.assertEqual(second_result.new_episode_updates, ())

        self.provider.videos_by_sec_uid["sec-one"] = [
            make_video("1004", "《末日重生》第17集", 5),
            *self.provider.videos_by_sec_uid["sec-one"],
        ]
        updated_result = await self.pipeline.sync_account(first_account["id"])
        self.assertEqual(updated_result.new_videos, 1)
        self.assertEqual(len(updated_result.new_episode_updates), 1)
        self.assertEqual(updated_result.new_episode_updates[0].episode["episode_number"], 17)
        self.assertEqual(self.repository.get_show(show["id"])["latest_episode"], 17)

    async def test_plain_video_is_saved_once_and_ignored_on_initial_sync(self):
        account, _ = await self.pipeline.add_account(self.account_one_provider.homepage_url)
        self.provider.videos_by_sec_uid["sec-one"] = [
            ProviderVideo(
                aweme_id="3001",
                description="这一集真的哭死我了😭",
                hashtags=("末日重生",),
                publish_time="2026-08-15T13:00:00+00:00",
                video_url="https://www.douyin.com/video/3001",
                cover_url=None,
                raw={"aweme_id": "3001"},
            )
        ]

        first_result = await self.pipeline.sync_account(account["id"])
        duplicate_result = await self.pipeline.sync_account(account["id"])
        videos = self.repository.list_videos(classification_status="ignored")
        self.assertEqual(first_result.review_videos, 0)
        self.assertEqual(first_result.ignored_videos, 1)
        self.assertEqual(duplicate_result.new_videos, 0)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["aweme_id"], "3001")
        self.assertEqual(videos[0]["classification_status"], "ignored")
        self.assertTrue(videos[0]["is_processed"])
        self.assertFalse(videos[0]["needs_review"])

    async def test_known_show_without_episode_is_sent_to_review(self):
        account, _ = await self.pipeline.add_account(self.account_one_provider.homepage_url)
        self.repository.create_show(title="末日重生", normalized_title="末日重生")
        self.provider.videos_by_sec_uid["sec-one"] = [
            ProviderVideo(
                aweme_id="3002",
                description="这一集真的哭死我了😭",
                hashtags=("末日重生",),
                publish_time="2026-08-15T13:02:00+00:00",
                video_url="https://www.douyin.com/video/3002",
                cover_url=None,
                raw={"aweme_id": "3002"},
            )
        ]

        result = await self.pipeline.sync_account(account["id"])
        review_video = self.repository.list_videos(classification_status="review")[0]

        self.assertEqual(result.review_videos, 1)
        self.assertEqual(result.ignored_videos, 0)
        self.assertEqual(review_video["aweme_id"], "3002")
        self.assertTrue(review_video["needs_review"])

    async def test_manual_ignore_marks_review_video_as_ignored(self):
        account, _ = await self.pipeline.add_account(self.account_one_provider.homepage_url)
        self.provider.videos_by_sec_uid["sec-one"] = [make_video("4002", "第十二集", 11)]
        await self.pipeline.sync_account(account["id"])
        review_video = self.repository.list_videos(classification_status="review")[0]

        ignored = self.pipeline.ignore_review(review_video["id"])

        self.assertEqual(ignored["classification_status"], "ignored")
        self.assertTrue(ignored["is_processed"])
        self.assertFalse(ignored["needs_review"])
        self.assertEqual(self.repository.counts()["pending_review"], 0)

    async def test_manual_review_creates_episode_and_marks_video_processed(self):
        account, _ = await self.pipeline.add_account(self.account_one_provider.homepage_url)
        self.provider.videos_by_sec_uid["sec-one"] = [
            make_video("4001", "第十二集", 10),
        ]
        await self.pipeline.sync_account(account["id"])
        review_video = self.repository.list_videos(needs_review=True)[0]

        result = self.pipeline.confirm_review(
            review_video["id"],
            new_show_title="末日重生",
            episode_number=12,
        )
        self.assertFalse(result.video["needs_review"])
        self.assertTrue(result.video["is_processed"])
        self.assertEqual(result.episode["episode_number"], 12)
        self.assertIsNotNone(result.update)


if __name__ == "__main__":
    unittest.main()
