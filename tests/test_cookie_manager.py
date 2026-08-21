from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from douyin_user_monitor.services.cookie_manager import CookieManager


class CookieManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cookies.json"
        self.reloaded: list[str] = []

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_header_and_browser_json_save_atomically_without_disclosure(self):
        async def probe(): return {"status": "healthy", "reason": "ok"}
        manager = CookieManager(self.path, reload_cookie=self.reloaded.append, test_cookie=probe)
        status = manager.save([{"name": "sessionid", "value": "example-value"}])
        self.assertTrue(status["configured"])
        self.assertNotIn("example-value", str(status))
        self.assertEqual(self.reloaded, ["sessionid=example-value"])
        tested = await manager.test()
        self.assertEqual(tested["status"], "healthy")
        self.assertNotIn("sessionid", str(tested))

    async def test_invalid_json_and_atomic_replace_failure_preserve_existing_file(self):
        manager = CookieManager(self.path, reload_cookie=self.reloaded.append)
        manager.save("sid=old")
        original = self.path.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON"):
            manager.save("[{broken")
        with patch("douyin_user_monitor.services.cookie_manager.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                manager.save("sid=new")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
