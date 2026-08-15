from __future__ import annotations

import unittest

from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.regex import chinese_number_to_int, normalize_title


class EpisodeParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = EpisodeParser()

    def parse(self, description: str, *, hashtags=(), shows=()):
        return self.parser.parse(
            description=description,
            hashtags=hashtags,
            account_nickname="AI剧场",
            known_shows=shows,
        )

    def assert_episode(self, description: str, title: str, number: int) -> None:
        result = self.parse(description)
        self.assertTrue(result.is_episode, result)
        self.assertEqual(result.show_title, title)
        self.assertEqual(result.episode_number, number)
        self.assertGreaterEqual(result.confidence, 0.8)

    def test_common_explicit_formats(self):
        cases = [
            ("《重生》第12集", "重生", 12),
            ("《重生》 第 12 集", "重生", 12),
            ("重生 第12集", "重生", 12),
            ("重生 12集", "重生", 12),
            ("重生 EP12", "重生", 12),
            ("重生 EP.12", "重生", 12),
            ("重生 Episode 12", "重生", 12),
            ("重生 12/100", "重生", 12),
            ("重生 12-100", "重生", 12),
            ("《末日重生》第三十五集", "末日重生", 35),
        ]
        for description, title, number in cases:
            with self.subTest(description=description):
                self.assert_episode(description, title, number)

    def test_bracketed_title_is_preferred_over_other_text(self):
        result = self.parse("AI剧场推荐：《末日重生》第28集来了！")
        self.assertEqual(result.show_title, "末日重生")
        self.assertEqual(result.episode_number, 28)
        self.assertEqual(result.method, "regex:bracketed")

    def test_known_alias_returns_canonical_show(self):
        result = self.parse(
            "重生首富 第27集",
            shows=[{"id": 3, "title": "重生后我成了首富", "aliases": ["重生首富"]}],
        )
        self.assertEqual(result.show_title, "重生后我成了首富")
        self.assertEqual(result.matched_show_id, 3)
        self.assertGreaterEqual(result.confidence, 0.95)

    def test_hashtag_can_supply_title_after_explicit_episode_number(self):
        result = self.parse("第27集来了", hashtags=["末日重生"])
        self.assertEqual(result.show_title, "末日重生")
        self.assertEqual(result.episode_number, 27)
        self.assertEqual(result.method, "regex:hashtag")

    def test_episode_without_title_is_not_auto_accepted(self):
        result = self.parse("第十二集")
        self.assertTrue(result.is_episode)
        self.assertEqual(result.episode_number, 12)
        self.assertIsNone(result.show_title)
        self.assertLess(result.confidence, 0.8)

    def test_video_without_explicit_episode_number_is_not_episode(self):
        result = self.parse("这一集真的哭死我了", hashtags=["末日重生"])
        self.assertFalse(result.is_episode)
        self.assertIsNone(result.episode_number)

    def test_normalization_and_chinese_numbers(self):
        self.assertEqual(normalize_title("《重生后，我成了首富！》"), "重生后我成了首富")
        cases = {"一": 1, "十二": 12, "二十一": 21, "三十五": 35, "一百": 100, "一百零二": 102}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(chinese_number_to_int(raw), expected)


if __name__ == "__main__":
    unittest.main()
