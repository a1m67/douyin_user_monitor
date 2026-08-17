from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from douyin_user_monitor.short_drama_settings import load_cookie_header, load_short_drama_settings


class ShortDramaSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        config_dir = self.root / "config"
        config_dir.mkdir()
        (config_dir / "douyin_web.example.yaml").write_text("TokenManager: {}", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_loads_required_environment_values_and_resolves_relative_paths(self):
        settings = load_short_drama_settings(
            project_root=self.root,
            environ={
                "DATABASE_URL": "sqlite:///data/custom.db",
                "CHECK_INTERVAL_MINUTES": "12",
                "MAX_CONCURRENT_CHECKS": "4",
                "INITIAL_SYNC_LIMIT": "25",
                "INCREMENTAL_FETCH_LIMIT": "31",
                "HISTORY_BACKFILL_PAGE_SIZE": "51",
                "NOTIFY_ON_INITIAL_SYNC": "true",
                "AUTO_ACCEPT_CONFIDENCE": "0.85",
                "LLM_ENABLED": "true",
                "LLM_API_KEY": "test-key",
                "LLM_BASE_URL": "https://llm.example/v1",
                "LLM_MODEL": "episode-model",
                "LLM_TIMEOUT_SECONDS": "12",
                "LLM_AUTO_ACCEPT_CONFIDENCE": "0.92",
            },
        )
        self.assertEqual(settings.database_path, (self.root / "data" / "custom.db").resolve())
        self.assertEqual(settings.check_interval_minutes, 12)
        self.assertEqual(settings.max_concurrent_checks, 4)
        self.assertEqual(settings.initial_sync_limit, 25)
        self.assertEqual(settings.incremental_fetch_limit, 31)
        self.assertEqual(settings.history_backfill_page_size, 51)
        self.assertTrue(settings.notify_on_initial_sync)
        self.assertEqual(settings.auto_accept_confidence, 0.85)
        self.assertTrue(settings.llm_enabled)
        self.assertEqual(settings.llm_base_url, "https://llm.example/v1")
        self.assertEqual(settings.llm_model, "episode-model")
        self.assertEqual(settings.llm_timeout_seconds, 12.0)
        self.assertEqual(settings.llm_auto_accept_confidence, 0.92)

    def test_enabled_llm_requires_connection_settings(self):
        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            load_short_drama_settings(
                project_root=self.root,
                environ={"LLM_ENABLED": "true"},
            )

    def test_cookie_reader_supports_browser_export_list_and_plain_header(self):
        json_path = self.root / "cookies.json"
        json_path.write_text('[{"name":"sid","value":"one"},{"name":"token","value":"two"}]', encoding="utf-8")
        header_path = self.root / "cookie.txt"
        header_path.write_text("sid=plain; token=value", encoding="utf-8")
        self.assertEqual(load_cookie_header(json_path), "sid=one; token=two")
        self.assertEqual(load_cookie_header(header_path), "sid=plain; token=value")


if __name__ == "__main__":
    unittest.main()
