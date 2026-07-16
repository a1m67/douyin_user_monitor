from __future__ import annotations

import unittest
from pathlib import Path

from douyin_user_monitor.settings import load_settings
from douyin_user_monitor.crawler.inprocess_client import InProcessDouyinClient


class InProcessCrawlerConfigTests(unittest.TestCase):
    def test_settings_require_crawler_config_not_upstream(self):
        settings = load_settings()
        self.assertTrue(settings.crawler.config_path.is_file())
        self.assertFalse(hasattr(settings, "upstream"))

    def test_inprocess_client_reads_headers_from_config(self):
        settings = load_settings()
        client = InProcessDouyinClient(settings.crawler.config_path)
        headers = self._run(client.get_douyin_headers())
        self.assertIn("headers", headers)
        self.assertIn("Cookie", headers["headers"])
        self.assertGreater(len(headers["headers"]["Cookie"]), 0)

    def test_no_http_upstream_client_module_api(self):
        # Hard-cut: shim may re-export, but no httpx base_url client remains as primary path.
        from douyin_user_monitor.api import monitor as monitor_api

        self.assertTrue(hasattr(monitor_api, "CRAWLER_CLIENT"))
        self.assertIsInstance(monitor_api.CRAWLER_CLIENT, InProcessDouyinClient)

    @staticmethod
    def _run(coro):
        import asyncio

        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
