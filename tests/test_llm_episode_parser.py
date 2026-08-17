from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.parsers.base import MATCHED, REVIEW
from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.llm import LLMParser, LLMTimeoutError
from douyin_user_monitor.providers.base import ProviderAccount, ProviderProfile, ProviderVideo
from douyin_user_monitor.providers.fake import FakeDouyinProvider
from douyin_user_monitor.repositories.sqlite import ShortDramaRepository
from douyin_user_monitor.services.episode_pipeline import ShortDramaPipeline


class FakeLLMClient:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, input_payload):
        self.calls.append(dict(input_payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def decision(
    *,
    title: str | None = "归墟",
    show_id: int | None = 3,
    episode: int | None = 9,
    content_type: str = "episode",
    confidence: float = 0.98,
    reason: str = "标题中的中文数字表示集数",
) -> str:
    return json.dumps(
        {
            "is_episode": content_type == "episode",
            "show_title": title,
            "show_id": show_id,
            "episode_number": episode,
            "content_type": content_type,
            "confidence": confidence,
            "reason": reason,
        },
        ensure_ascii=False,
    )


class LLMParserTests(unittest.TestCase):
    known_shows = [{"id": 3, "title": "归墟", "aliases": ["归墟系列"]}]

    def parse(self, description: str, response: str | Exception):
        client = FakeLLMClient(response)
        parser = EpisodeParser(
            llm_backend=LLMParser(client),
            auto_accept_confidence=0.8,
        )
        result = parser.parse(
            display_title=description,
            description=description,
            account_nickname="末日故事",
            known_shows=self.known_shows,
            recent_account_videos=[{"display_title": "《归墟》第8集"}],
        )
        return result, client

    def test_chinese_nine_falls_back_to_llm(self):
        result, client = self.parse("原创末日故事连载【归墟】九-食物链", decision(episode=9))

        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.show_title, "归墟")
        self.assertEqual(result.episode_number, 9)
        self.assertEqual(result.method, "llm")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["display_title"], "原创末日故事连载【归墟】九-食物链")
        self.assertEqual(client.calls[0]["known_shows"], self.known_shows)
        self.assertIn("regex_result", client.calls[0])
        self.assertEqual(len(client.calls[0]["recent_account_videos"]), 1)

    def test_chinese_twelve_falls_back_to_llm(self):
        result, _ = self.parse("原创末日故事连载【归墟】十二-终局", decision(episode=12))
        self.assertEqual((result.status, result.episode_number), (MATCHED, 12))

    def test_trailer_never_becomes_episode_zero(self):
        result, _ = self.parse(
            "《归墟》第二季先导片",
            decision(
                episode=None,
                content_type="trailer",
                confidence=0.97,
                reason="先导片不是明确的第0集",
            ),
        )
        self.assertEqual(result.status, REVIEW)
        self.assertEqual(result.content_type, "trailer")
        self.assertIsNone(result.episode_number)

    def test_explicit_episode_zero_is_valid_and_skips_llm(self):
        client = FakeLLMClient(decision(episode=99))
        parser = EpisodeParser(llm_backend=LLMParser(client))
        result = parser.parse(
            description="《归墟》第0集",
            known_shows=self.known_shows,
        )
        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.episode_number, 0)
        self.assertEqual(len(client.calls), 0)

    def test_timeout_and_invalid_json_fall_back_to_review(self):
        for response, expected_reason in (
            (LLMTimeoutError("timeout"), "llm_timeout"),
            ("not json", "llm_invalid_response"),
        ):
            with self.subTest(expected_reason=expected_reason):
                result, _ = self.parse("原创末日故事连载【归墟】九-食物链", response)
                self.assertEqual(result.status, REVIEW)
                self.assertEqual(result.reason, expected_reason)
                self.assertEqual(result.method, "llm")

    def test_low_confidence_and_new_show_stay_in_review(self):
        low, _ = self.parse("【归墟】九-食物链", decision(confidence=0.6))
        new_show, _ = self.parse(
            "【不存在的新剧】九-食物链",
            decision(title="不存在的新剧", show_id=None),
        )
        self.assertEqual(low.status, REVIEW)
        self.assertEqual(new_show.status, REVIEW)
        self.assertIsNone(new_show.matched_show_id)

    def test_high_confidence_regex_match_never_calls_llm(self):
        client = FakeLLMClient(decision(episode=99))
        parser = EpisodeParser(llm_backend=LLMParser(client))
        result = parser.parse(
            description="《归墟》第9集",
            known_shows=self.known_shows,
        )
        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.episode_number, 9)
        self.assertEqual(len(client.calls), 0)

    def test_obvious_non_drama_never_calls_llm(self):
        client = FakeLLMClient(decision(episode=99))
        parser = EpisodeParser(llm_backend=LLMParser(client))
        result = parser.parse(description="今天去公园散步，天气很好")
        self.assertEqual(result.status, "ignored")
        self.assertEqual(len(client.calls), 0)


class LLMPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_show_high_confidence_auto_archives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ShortDramaRepository(Path(temp_dir) / "app.db")
            show = repository.create_show(title="归墟", normalized_title="归墟")
            provider_account = ProviderAccount(
                id="",
                sec_uid="llm-sec",
                homepage_url="https://www.douyin.com/user/llm-sec",
            )
            provider = FakeDouyinProvider(
                accounts_by_url={provider_account.homepage_url: provider_account},
                profiles_by_sec_uid={"llm-sec": ProviderProfile(nickname="末日故事")},
                videos_by_sec_uid={
                    "llm-sec": [
                        ProviderVideo(
                            aweme_id="llm-9",
                            description="原创末日故事连载【归墟】九-食物链",
                            hashtags=(),
                            publish_time="2026-08-17T00:00:00+00:00",
                            video_url="https://www.douyin.com/video/llm-9",
                            cover_url=None,
                            raw={},
                        )
                    ]
                },
            )
            client = FakeLLMClient(decision(show_id=show["id"], episode=9))
            parser = EpisodeParser(llm_backend=LLMParser(client))
            pipeline = ShortDramaPipeline(
                repository=repository,
                provider=provider,
                parser=parser,
            )

            account, _ = await pipeline.add_account(provider_account.homepage_url)
            result = await pipeline.sync_account(account["id"])
            stored = repository.get_video_by_aweme_id("llm-9")

            self.assertEqual(result.review_videos, 0)
            self.assertEqual(repository.get_show_episodes(show["id"])[0]["episode_number"], 9)
            self.assertEqual(stored["parser_method"], "llm")
            self.assertEqual(stored["llm_raw_result"]["confidence"], 0.98)


class EpisodeZeroMigrationTests(unittest.TestCase):
    def test_v5_episode_check_migrates_to_allow_zero_and_reject_negative(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy.db"
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO app_meta(key, value) VALUES ('schema_version', '5');
                CREATE TABLE episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    show_id INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
                    episode_number INTEGER NOT NULL,
                    first_video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
                    first_account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                    published_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(show_id, episode_number),
                    CHECK (episode_number > 0)
                );
                """
            )
            connection.close()

            repository = ShortDramaRepository(database_path)
            account = repository.create_account(
                sec_uid="episode-zero",
                nickname="零集测试",
                homepage_url="https://www.douyin.com/user/episode-zero",
            )
            show = repository.create_show(title="归墟", normalized_title="归墟")
            video, _ = repository.create_video(
                aweme_id="episode-zero-video",
                account_id=account["id"],
                description="《归墟》第0集",
                hashtags=(),
                publish_time=None,
                video_url="",
                cover_url=None,
                raw={},
            )

            written = repository.record_episode_source(
                show_id=show["id"],
                episode_number=0,
                video_id=video["id"],
                account_id=account["id"],
                published_at=None,
            )

            self.assertEqual(written.episode["episode_number"], 0)
            with self.assertRaises(ValueError):
                repository.record_episode_source(
                    show_id=show["id"],
                    episode_number=-1,
                    video_id=video["id"],
                    account_id=account["id"],
                    published_at=None,
                )


if __name__ == "__main__":
    unittest.main()
