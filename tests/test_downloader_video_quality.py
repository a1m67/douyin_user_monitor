import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.monitor.downloader import AwemeAssetDownloader
from douyin_user_monitor.monitor.notifier import NoopMonitorNotifier
from douyin_user_monitor.monitor.user_sync import UserSyncService


class DownloaderVideoQualityTests(unittest.TestCase):
    def build_downloader(self) -> AwemeAssetDownloader:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        return AwemeAssetDownloader(Path(self.temp_dir.name))

    def test_extract_video_source_selects_highest_resolution(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "bit_rate": [
                        {
                            "bit_rate": 1_000_000,
                            "gear_name": "normal_720",
                            "play_addr": {"url_list": ["https://cdn/720.mp4"], "width": 720, "height": 1280},
                        },
                        {
                            "bit_rate": 3_000_000,
                            "gear_name": "normal_1080",
                            "play_addr": {"url_list": ["https://cdn/1080.mp4"], "width": 1080, "height": 1920},
                        },
                    ]
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/1080.mp4")
        self.assertEqual(source.bit_rate, 3_000_000)
        self.assertEqual(source.width, 1080)
        self.assertEqual(source.height, 1920)
        self.assertEqual(source.gear_name, "normal_1080")

    def test_extract_video_source_prefers_resolution_over_higher_bitrate(self):
        """Real Douyin pattern: 540p peak bitrate can exceed 1080p."""
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "bit_rate": [
                        {
                            "bit_rate": 829_980,
                            "gear_name": "normal_540_0",
                            "play_addr": {"url_list": ["https://cdn/540.mp4"], "width": 576, "height": 1024},
                        },
                        {
                            "bit_rate": 816_993,
                            "gear_name": "normal_1080_0",
                            "play_addr": {"url_list": ["https://cdn/1080.mp4"], "width": 1080, "height": 1920},
                        },
                        {
                            "bit_rate": 662_534,
                            "gear_name": "normal_720_0",
                            "play_addr": {"url_list": ["https://cdn/720.mp4"], "width": 720, "height": 1280},
                        },
                    ]
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/1080.mp4")
        self.assertEqual(source.gear_name, "normal_1080_0")
        self.assertEqual(source.width, 1080)
        self.assertEqual(source.height, 1920)

    def test_extract_video_source_uses_bit_rate_when_resolution_ties(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "bit_rate": [
                        {
                            "bit_rate": 1_000_000,
                            "gear_name": "normal_1080_a",
                            "play_addr": {"url_list": ["https://cdn/1080-a.mp4"], "width": 1080, "height": 1920},
                        },
                        {
                            "bit_rate": 2_500_000,
                            "gear_name": "normal_1080_b",
                            "play_addr": {"url_list": ["https://cdn/1080-b.mp4"], "width": 1080, "height": 1920},
                        },
                    ]
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/1080-b.mp4")
        self.assertEqual(source.bit_rate, 2_500_000)

    def test_extract_video_source_prefers_non_low_gear_on_full_tie(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "bit_rate": [
                        {
                            "bit_rate": 800_000,
                            "gear_name": "low_720_0",
                            "play_addr": {"url_list": ["https://cdn/low-720.mp4"], "width": 720, "height": 1280},
                        },
                        {
                            "bit_rate": 800_000,
                            "gear_name": "normal_720_0",
                            "play_addr": {"url_list": ["https://cdn/normal-720.mp4"], "width": 720, "height": 1280},
                        },
                    ]
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/normal-720.mp4")
        self.assertEqual(source.gear_name, "normal_720_0")

    def test_extract_video_source_selects_4k_adapt_gear(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "bit_rate": [
                        {
                            "bit_rate": 1_500_000,
                            "gear_name": "normal_1080_0",
                            "play_addr": {"url_list": ["https://cdn/1080.mp4"], "width": 1080, "height": 1920},
                        },
                        {
                            "bit_rate": 3_061_169,
                            "gear_name": "adapt_lowest_4_1",
                            "play_addr": {"url_list": ["https://cdn/4k.mp4"], "width": 2160, "height": 3840},
                        },
                    ],
                    "play_addr": {"url_list": ["https://cdn/default.mp4"], "width": 1080, "height": 1920},
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/4k.mp4")
        self.assertEqual(source.gear_name, "adapt_lowest_4_1")
        self.assertEqual(source.width, 2160)

    def test_extract_video_source_falls_back_without_bit_rate(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "play_addr": {"url_list": ["https://cdn/fallback.mp4"], "width": 576, "height": 1024},
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/fallback.mp4")
        self.assertEqual(source.gear_name, "play_addr")

    def test_extract_video_source_fallback_picks_clearest_top_level_addr(self):
        downloader = self.build_downloader()
        source = downloader._extract_video_source(
            {
                "video": {
                    "play_addr": {"url_list": ["https://cdn/play.mp4"], "width": 720, "height": 1280},
                    "play_addr_h264": {"url_list": ["https://cdn/h264.mp4"], "width": 1080, "height": 1920},
                    "download_addr": {"url_list": ["https://cdn/dl.mp4"], "width": 720, "height": 720},
                }
            }
        )

        self.assertIsNotNone(source)
        self.assertEqual(source.url, "https://cdn/h264.mp4")
        self.assertEqual(source.gear_name, "play_addr_h264")
        self.assertEqual(source.width, 1080)

    def test_download_record_keeps_selected_video_source(self):
        service = UserSyncService(crawler=None, downloader=None, notifier=NoopMonitorNotifier())
        record = service._build_download_record(
            {"aweme_id": "1", "create_time": 1_710_000_000, "video": {"duration": 1000}},
            {
                "media_type": "video",
                "files": ["user/a.mp4"],
                "downloaded_file_count": 1,
                "existing_file_count": 0,
                "total_size_bytes": 128,
                "video_source": {"bit_rate": 3_000_000, "width": 1080, "height": 1920, "gear_name": "normal_1080"},
            },
        )

        self.assertEqual(record["video_source"]["bit_rate"], 3_000_000)
        self.assertEqual(record["video_source"]["width"], 1080)
        self.assertEqual(record["video_source"]["height"], 1920)


if __name__ == "__main__":
    unittest.main()
