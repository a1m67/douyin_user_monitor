from __future__ import annotations

import tempfile
import unittest
import asyncio
import time
from pathlib import Path

import httpx

from douyin_user_monitor.notifiers.base import EpisodeNotification
from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.notifiers.telegram import TelegramNotifier, format_episode_update
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import EpisodeUpdate
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline
from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile, ProviderVideo
from douyin_user_monitor.providers.fake import FakeDouyinProvider


class SuccessfulNotifier:
    channel = "success"

    def __init__(self) -> None:
        self.sent: list[EpisodeNotification] = []

    async def send_episode_update(self, notification: EpisodeNotification) -> None:
        self.sent.append(notification)

    async def aclose(self) -> None:
        return None


class FailingNotifier:
    channel = "failure"

    async def send_episode_update(self, notification: EpisodeNotification) -> None:
        _ = notification
        raise RuntimeError("webhook unavailable")

    async def aclose(self) -> None:
        return None


class EpisodeNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        repository = ShortDramaRepository(Path(self.temp_dir.name) / "app.db")
        account = repository.create_account(
            sec_uid="sec-1",
            nickname="AI剧场",
            homepage_url="https://www.douyin.com/user/sec-1",
        )
        video, _ = repository.create_video(
            aweme_id="1001",
            account_id=account["id"],
            description="《末日重生》第17集",
            hashtags=[],
            publish_time="2026-08-15T12:31:00+00:00",
            video_url="https://www.douyin.com/video/1001",
            cover_url="https://cover.example/1001.jpg",
            raw={},
        )
        show = repository.create_show(title="末日重生", normalized_title="末日重生")
        write = repository.record_episode_source(
            show_id=show["id"],
            episode_number=17,
            video_id=video["id"],
            account_id=account["id"],
            published_at=video["publish_time"],
        )
        self.repository = repository
        self.update = EpisodeUpdate(show=show, episode=write.episode, video=video, account=account)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_dispatch_only_enqueues_until_worker_delivers(self):
        successful = SuccessfulNotifier()
        dispatcher = NotificationDispatcher(
            repository=self.repository,
            notifiers=[successful, FailingNotifier()],
        )

        results = await dispatcher.dispatch(self.update)

        self.assertEqual([(item.channel, item.success) for item in results], [("success", False), ("failure", False)])
        self.assertEqual(successful.sent, [])
        self.assertEqual(await dispatcher.deliver_due(), 1)
        self.assertEqual(len(successful.sent), 1)
        persisted = self.repository.list_notifications(episode_id=self.update.episode["id"])
        self.assertEqual({(item["channel"], item["success"]) for item in persisted}, {("success", True), ("failure", False)})
        self.assertEqual(self.repository.get_show_episodes(self.update.show["id"])[0]["episode_number"], 17)

    async def test_dispatch_is_idempotent_per_episode_and_channel(self):
        successful = SuccessfulNotifier()
        dispatcher = NotificationDispatcher(repository=self.repository, notifiers=[successful])

        await dispatcher.dispatch(self.update)
        await dispatcher.dispatch(self.update)
        await dispatcher.deliver_due()

        self.assertEqual(len(successful.sent), 1)
        deliveries = self.repository.list_notification_deliveries(episode_id=self.update.episode["id"])
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["status"], "sent")

    async def test_failed_delivery_retries_and_eventually_succeeds(self):
        class FlakyNotifier(SuccessfulNotifier):
            channel = "flaky"

            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            async def send_episode_update(self, notification: EpisodeNotification) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary")
                await super().send_episode_update(notification)

        notifier = FlakyNotifier()
        dispatcher = NotificationDispatcher(repository=self.repository, notifiers=[notifier], max_attempts=3)
        await dispatcher.dispatch(self.update)
        self.assertEqual(await dispatcher.deliver_due(), 0)
        delivery = self.repository.list_notification_deliveries()[0]
        self.assertEqual(delivery["status"], "retry")

        with self.repository._write_transaction() as connection:
            connection.execute("UPDATE notification_deliveries SET next_attempt_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(await dispatcher.deliver_due(), 1)
        self.assertEqual(self.repository.list_notification_deliveries()[0]["status"], "sent")

    async def test_stale_processing_delivery_is_recovered(self):
        successful = SuccessfulNotifier()
        dispatcher = NotificationDispatcher(repository=self.repository, notifiers=[successful], claim_timeout_seconds=1)
        await dispatcher.dispatch(self.update)
        with self.repository._write_transaction() as connection:
            connection.execute(
                "UPDATE notification_deliveries SET status='processing', sent_at=NULL, locked_at='2000-01-01T00:00:00+00:00'"
            )
        self.assertEqual(await dispatcher.deliver_due(), 1)

    async def test_max_attempts_marks_delivery_dead(self):
        dispatcher = NotificationDispatcher(repository=self.repository, notifiers=[FailingNotifier()], max_attempts=1)
        await dispatcher.dispatch(self.update)
        await dispatcher.deliver_due()
        self.assertEqual(self.repository.list_notification_deliveries()[0]["status"], "dead")

    async def test_episode_and_delivery_are_committed_in_one_transaction(self):
        account = self.update.account
        video, _ = self.repository.create_video(
            aweme_id="transactional-episode",
            account_id=account["id"],
            description="《末日重生》第18集",
            hashtags=[],
            publish_time="2026-08-15T12:32:00+00:00",
            video_url="https://www.douyin.com/video/transactional-episode",
            cover_url=None,
            raw={},
        )
        write = self.repository.record_episode_source(
            show_id=self.update.show["id"],
            episode_number=18,
            video_id=video["id"],
            account_id=account["id"],
            published_at=video["publish_time"],
            notification_channels=("telegram", "feishu"),
            notification_payload={
                "show_title": "末日重生", "season_number": 1, "episode_number": 18,
                "account_nickname": account["nickname"], "published_at": video["publish_time"],
                "video_url": video["video_url"], "cover_url": None,
            },
        )

        deliveries = self.repository.list_notification_deliveries(episode_id=write.episode["id"])
        self.assertEqual([item["channel"] for item in deliveries], ["telegram", "feishu"])
        self.assertTrue(all(item["status"] == "pending" for item in deliveries))

    async def test_delivery_serialization_failure_rolls_back_episode_and_source(self):
        video, _ = self.repository.create_video(
            aweme_id="transaction-rollback",
            account_id=self.update.account["id"],
            description="《末日重生》第19集",
            hashtags=[], publish_time=None,
            video_url="https://www.douyin.com/video/transaction-rollback",
            cover_url=None, raw={},
        )
        with self.assertRaises(TypeError):
            self.repository.record_episode_source(
                show_id=self.update.show["id"], episode_number=19,
                video_id=video["id"], account_id=self.update.account["id"], published_at=None,
                notification_channels=("telegram",),
                notification_payload={"invalid": {object()}},
            )

        self.assertFalse(any(item["episode_number"] == 19 for item in self.repository.get_show_episodes(self.update.show["id"])))
        self.assertEqual(self.repository.list_notification_deliveries(), [])

    async def test_duplicate_episode_source_does_not_duplicate_delivery(self):
        payload = {
            "show_title": "末日重生", "season_number": 1, "episode_number": 18,
            "account_nickname": self.update.account["nickname"], "published_at": None,
            "video_url": "", "cover_url": None,
        }
        first_video, _ = self.repository.create_video(
            aweme_id="duplicate-source-a", account_id=self.update.account["id"],
            description="第18集", hashtags=[], publish_time=None, video_url="", cover_url=None, raw={},
        )
        second_video, _ = self.repository.create_video(
            aweme_id="duplicate-source-b", account_id=self.update.account["id"],
            description="第18集转载", hashtags=[], publish_time=None, video_url="", cover_url=None, raw={},
        )
        first = self.repository.record_episode_source(
            show_id=self.update.show["id"], episode_number=18, video_id=first_video["id"],
            account_id=self.update.account["id"], published_at=None,
            notification_channels=("telegram",), notification_payload=payload,
        )
        second = self.repository.record_episode_source(
            show_id=self.update.show["id"], episode_number=18, video_id=second_video["id"],
            account_id=self.update.account["id"], published_at=None,
            notification_channels=("telegram",), notification_payload=payload,
        )

        self.assertTrue(first.is_new_episode)
        self.assertFalse(second.is_new_episode)
        self.assertEqual(len(self.repository.list_notification_deliveries(episode_id=first.episode["id"])), 1)

    async def test_persisted_delivery_is_sent_after_worker_restart(self):
        successful = SuccessfulNotifier()
        queued = NotificationDispatcher(repository=self.repository, notifiers=[successful])
        await queued.dispatch(self.update)

        restarted = NotificationDispatcher(repository=self.repository, notifiers=[successful], poll_seconds=60)
        await restarted.start()
        for _ in range(50):
            if successful.sent:
                break
            await asyncio.sleep(0.01)
        await restarted.stop()

        self.assertEqual(len(successful.sent), 1)
        self.assertEqual(self.repository.list_notification_deliveries()[0]["status"], "sent")

    async def test_slow_notifier_does_not_delay_incremental_sync(self):
        release = asyncio.Event()

        class SlowNotifier(SuccessfulNotifier):
            channel = "slow"

            async def send_episode_update(self, notification: EpisodeNotification) -> None:
                await release.wait()
                await super().send_episode_update(notification)

        account_ref = ProviderAccount(id="", sec_uid="slow-sec", homepage_url="https://www.douyin.com/user/slow")
        provider = FakeDouyinProvider(
            accounts_by_url={account_ref.homepage_url: account_ref},
            profiles_by_sec_uid={"slow-sec": ProviderProfile(nickname="慢通知剧场")},
            videos_by_sec_uid={"slow-sec": []},
        )
        notifier = SlowNotifier()
        dispatcher = NotificationDispatcher(repository=self.repository, notifiers=[notifier], poll_seconds=60)
        pipeline = ShortDramaPipeline(repository=self.repository, provider=provider, dispatcher=dispatcher)
        account, _ = await pipeline.add_account(account_ref.homepage_url)
        await pipeline.sync_account(account["id"])
        provider.videos_by_sec_uid["slow-sec"] = [ProviderVideo(
            aweme_id="slow-notifier-1", description="《慢通知短剧》第1集", hashtags=(),
            publish_time="2026-08-15T12:40:00+00:00",
            video_url="https://www.douyin.com/video/slow-notifier-1", cover_url=None,
            raw={"aweme_id": "slow-notifier-1"},
        )]
        await dispatcher.start()
        started = time.perf_counter()
        result = await asyncio.wait_for(pipeline.sync_account(account["id"]), timeout=0.5)
        elapsed = time.perf_counter() - started
        release.set()
        await dispatcher.stop()

        self.assertEqual(len(result.new_episode_updates), 1)
        self.assertLess(elapsed, 0.5)

    async def test_initial_sync_never_enqueues_even_with_legacy_setting_enabled(self):
        account_ref = ProviderAccount(id="", sec_uid="initial-sec", homepage_url="https://www.douyin.com/user/initial")
        provider = FakeDouyinProvider(
            accounts_by_url={account_ref.homepage_url: account_ref},
            profiles_by_sec_uid={"initial-sec": ProviderProfile(nickname="初始剧场")},
            videos_by_sec_uid={"initial-sec": [ProviderVideo(
                aweme_id="initial-no-notify", description="《初始短剧》第1集", hashtags=(),
                publish_time="2026-08-15T12:41:00+00:00",
                video_url="https://www.douyin.com/video/initial-no-notify", cover_url=None,
                raw={"aweme_id": "initial-no-notify"},
            )]},
        )
        dispatcher = NotificationDispatcher(
            repository=self.repository, notifiers=[SuccessfulNotifier()]
        )
        pipeline = ShortDramaPipeline(
            repository=self.repository, provider=provider, dispatcher=dispatcher,
            notify_on_initial_sync=True,
        )
        account, _ = await pipeline.add_account(account_ref.homepage_url)

        result = await pipeline.sync_account(account["id"])

        self.assertTrue(result.initial_sync)
        self.assertEqual(result.new_episode_updates, ())
        self.assertEqual(self.repository.list_notification_deliveries(), [])

    async def test_one_channel_failure_does_not_block_another(self):
        successful = SuccessfulNotifier()
        dispatcher = NotificationDispatcher(
            repository=self.repository,
            notifiers=[FailingNotifier(), successful],
        )
        await dispatcher.dispatch(self.update)

        self.assertEqual(await dispatcher.deliver_due(), 1)
        statuses = {item["channel"]: item["status"] for item in self.repository.list_notification_deliveries()}
        self.assertEqual(statuses, {"failure": "retry", "success": "sent"})

    async def test_telegram_uses_cover_photo_when_available(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"ok": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        notifier = TelegramNotifier(bot_token="token", chat_id="chat", client=client)
        notification = EpisodeNotification.from_update(self.update)
        await notifier.send_episode_update(notification)
        await client.aclose()

        self.assertEqual(requests[0].url.path, "/bottoken/sendPhoto")
        self.assertIn("短剧更新", format_episode_update(notification))
        self.assertIn("第 17 集", format_episode_update(notification))


if __name__ == "__main__":
    unittest.main()
