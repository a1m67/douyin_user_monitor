from __future__ import annotations

import json
import unittest
from pathlib import Path

from douyin_user_monitor.parsers.base import MATCHED, REVIEW
from douyin_user_monitor.parsers.episode_parser import EpisodeParser


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "real_world_titles.json"


def recent_matches(show_title: str, *numbers: int) -> list[dict]:
    return [
        {
            "show_id": 11,
            "show_title": show_title,
            "aliases": [],
            "episode_number": number,
            "publish_time": f"2026-08-15T12:{index:02d}:00+00:00",
        }
        for index, number in enumerate(numbers)
    ]


class RealWorldEpisodeTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = EpisodeParser()
        self.samples = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_real_world_title_fixture(self) -> None:
        for sample in self.samples:
            with self.subTest(sample=sample["name"]):
                result = self.parser.parse(
                    description=sample["description"],
                    hashtags=sample.get("hashtags", []),
                    account_nickname="真理之羊",
                    known_shows=sample.get("known_shows", []),
                )
                for field, expected in sample["expected"].items():
                    self.assertEqual(getattr(result, field), expected, result)

    def test_bare_number_resolves_from_unambiguous_account_sequence(self) -> None:
        result = self.parser.parse(
            description="41 车与船",
            account_nickname="真理之羊",
            recent_account_matches=recent_matches("国王战", 38, 39, 40),
            account_show_candidates=[{"id": 11, "title": "国王战", "aliases": []}],
        )
        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.show_title, "国王战")
        self.assertEqual(result.episode_number, 41)
        self.assertEqual(result.method, "context:account_sequence")
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_suffix_number_resolves_from_unambiguous_account_sequence(self) -> None:
        result = self.parser.parse(
            description="船登陆40 #动漫 #动画",
            account_nickname="真理之羊",
            recent_account_matches=recent_matches("国王战", 38, 39),
            account_show_candidates=[{"id": 11, "title": "国王战", "aliases": []}],
        )
        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.show_title, "国王战")
        self.assertEqual(result.episode_number, 40)

    def test_seedance_bare_number_resolves_from_recent_sequence(self) -> None:
        result = self.parser.parse(
            description="39 本视频由小云雀Seedance2.0创作生成",
            hashtags=["小云雀AI", "杨间", "叶真"],
            account_nickname="真理之羊",
            recent_account_matches=recent_matches("国王战", 37, 38),
            account_show_candidates=[{"id": 11, "title": "国王战", "aliases": []}],
        )
        self.assertEqual(result.status, MATCHED)
        self.assertEqual(result.show_title, "国王战")
        self.assertEqual(result.episode_number, 39)

    def test_interleaved_multiple_shows_stay_in_review(self) -> None:
        result = self.parser.parse(
            description="41 车与船",
            account_nickname="真理之羊",
            recent_account_matches=[
                *recent_matches("A", 38, 39),
                *[
                    {
                        "show_id": 12,
                        "show_title": "B",
                        "aliases": [],
                        "episode_number": number,
                    }
                    for number in (12, 13)
                ],
            ],
        )
        self.assertEqual(result.status, REVIEW)
        self.assertEqual(result.episode_candidate, 41)


if __name__ == "__main__":
    unittest.main()
