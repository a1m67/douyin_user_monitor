from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from douyin_user_monitor.notifiers.base import EpisodeNotification
from douyin_user_monitor.notifiers.dispatcher import NotificationDispatcher
from douyin_user_monitor.notifiers.telegram import TelegramNotifier, format_episode_update
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import EpisodeUpdate


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

    async def test_dispatch_records_success_and_failure_without_removing_episode(self):
        successful = SuccessfulNotifier()
        dispatcher = NotificationDispatcher(
            repository=self.repository,
            notifiers=[successful, FailingNotifier()],
        )

        results = await dispatcher.dispatch(self.update)

        self.assertEqual([(item.channel, item.success) for item in results], [("success", True), ("failure", False)])
        self.assertEqual(len(successful.sent), 1)
        persisted = self.repository.list_notifications(episode_id=self.update.episode["id"])
        self.assertEqual({(item["channel"], item["success"]) for item in persisted}, {("success", True), ("failure", False)})
        self.assertEqual(self.repository.get_show_episodes(self.update.show["id"])[0]["episode_number"], 17)

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
