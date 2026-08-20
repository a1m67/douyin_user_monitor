from __future__ import annotations

import unittest

from douyin_user_monitor.parsers.base import IGNORED, MATCHED, REVIEW
from douyin_user_monitor.parsers.episode_parser import EpisodeParser
from douyin_user_monitor.parsers.regex import chinese_number_to_int, normalize_title


class EpisodeParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = EpisodeParser()

    def parse(self, description: str, *, hashtags=(), shows=(), text_sources=None):
        return self.parser.parse(
            description=description,
            hashtags=hashtags,
            account_nickname="AI剧场",
            known_shows=shows,
            text_sources=text_sources,
        )

    def assert_episode(self, description: str, title: str, number: int) -> None:
        result = self.parse(description)
        self.assertEqual(result.status, MATCHED, result)
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

    def test_multi_season_formats(self):
        cases = [
            ("《归墟》第二季第12集", 2, 12),
            ("《归墟》第 2 季 第12集", 2, 12),
            ("《归墟》S2E12", 2, 12),
            ("《归墟》S02 EP12", 2, 12),
            ("《归墟》Season 2 Episode 12", 2, 12),
        ]
        for description, season, episode in cases:
            with self.subTest(description=description):
                result = self.parse(description)
                self.assertEqual(result.status, MATCHED, result)
                self.assertEqual(result.show_title, "归墟")
                self.assertEqual(result.season_number, season)
                self.assertEqual(result.episode_number, episode)

    def test_second_season_trailer_does_not_become_episode_zero(self):
        result = self.parse("《归墟》第二季先导片")
        self.assertEqual(result.status, REVIEW)
        self.assertEqual(result.show_title, "归墟")
        self.assertEqual(result.season_number, 2)
        self.assertIsNone(result.episode_number)
        self.assertEqual(result.content_type, "trailer")

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
        self.assertEqual(result.status, REVIEW)
        self.assertTrue(result.is_episode)
        self.assertEqual(result.episode_number, 12)
        self.assertIsNone(result.show_title)
        self.assertLess(result.confidence, 0.8)

    def test_video_without_explicit_episode_number_is_not_episode(self):
        result = self.parse("这一集真的哭死我了", hashtags=["末日重生"])
        self.assertEqual(result.status, IGNORED)
        self.assertFalse(result.is_episode)
        self.assertIsNone(result.episode_number)
        self.assertEqual(result.reason, "no_short_drama_or_episode_signal")

    def test_known_show_without_episode_requires_review(self):
        result = self.parse(
            "这一集真的哭死我了",
            hashtags=["末日重生"],
            shows=[{"id": 3, "title": "末日重生", "aliases": ["末日"]}],
        )
        self.assertEqual(result.status, REVIEW)
        self.assertEqual(result.show_title, "末日重生")
        self.assertIsNone(result.episode_number)
        self.assertEqual(result.reason, "known_show_without_episode")

    def test_bracketed_short_drama_context_without_episode_requires_review(self):
        result = self.parse("短剧《重生后我成了首富》持续更新")
        self.assertEqual(result.status, REVIEW)
        self.assertEqual(result.show_title, "重生后我成了首富")
        self.assertIsNone(result.episode_number)

    def test_confirmed_douyin_title_sources_match_and_record_evidence(self):
        result = self.parse(
            "原创ai漫剧《契鬼人》义庄副本第一夜 全片由小云雀Seedance2.5制作",
            text_sources={
                "series_play_info.item_title_prefix.text": "第8集",
                "item_title": "原创ai漫剧《契鬼人》义庄副本第一夜",
                "desc": "原创ai漫剧《契鬼人》义庄副本第一夜 全片由小云雀Seedance2.5制作",
            },
        )

        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.show_title, "契鬼人")
        self.assertEqual(result.episode_number, 8)
        self.assertEqual(result.episode_evidence["source_field"], "series_play_info.item_title_prefix.text")
        self.assertEqual(result.show_evidence["source_field"], "item_title")

    def test_decimal_versions_do_not_become_bare_episode_candidates(self):
        target = self.parse("原创ai漫剧《契鬼人》义庄副本第一夜 Seedance2.5制作")
        self.assertEqual(target.status, REVIEW)
        self.assertEqual(target.show_title_candidate, "契鬼人")
        self.assertIsNone(target.episode_candidate)

        for description in ("2.5", "2.0", "V2.1", "GPT5.6", "Seedance2.5"):
            with self.subTest(description=description):
                result = self.parse(description)
                self.assertIsNone(result.episode_candidate)

    def test_bare_number_candidates_score_context_and_reject_counts(self):
        cases = [
            ("39 本视频由小云雀Seedance2.0创作生成", 39),
            ("船登陆40", 40),
            ("人间夜游 李乐平 01 我是李乐平", 1),
        ]
        for description, expected in cases:
            with self.subTest(description=description):
                result = self.parse(description)
                self.assertEqual(result.status, REVIEW)
                self.assertEqual(result.episode_candidate, expected)
                self.assertEqual(result.episode_evidence["value"], expected)

        for description in ("2026新年快乐", "100万播放", "3种AI技巧"):
            with self.subTest(description=description):
                result = self.parse(description)
                self.assertEqual(result.status, IGNORED)
                self.assertIsNone(result.episode_candidate)

    def test_normalization_and_chinese_numbers(self):
        self.assertEqual(normalize_title("《重生后，我成了首富！》"), "重生后我成了首富")
        cases = {"一": 1, "十二": 12, "二十一": 21, "三十五": 35, "一百": 100, "一百零二": 102}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(chinese_number_to_int(raw), expected)


if __name__ == "__main__":
    unittest.main()
