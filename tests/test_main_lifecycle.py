from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from douyin_user_monitor import main


class MainLifecycleTests(IsolatedAsyncioTestCase):
    async def test_default_does_not_start_or_stop_legacy_monitor(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(legacy_monitor_enabled=False),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )
        legacy = SimpleNamespace(auto_resume=AsyncMock(), shutdown=AsyncMock())
        with patch.object(main, "SHORT_DRAMA_RUNTIME", runtime), patch.object(
            main, "monitor_service", legacy
        ):
            await main.startup_monitor()
            await main.shutdown_monitor()
        runtime.start.assert_awaited_once()
        runtime.shutdown.assert_awaited_once()
        legacy.auto_resume.assert_not_awaited()
        legacy.shutdown.assert_not_awaited()

    async def test_explicit_setting_keeps_legacy_lifecycle_available(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(legacy_monitor_enabled=True),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )
        legacy = SimpleNamespace(auto_resume=AsyncMock(), shutdown=AsyncMock())
        with patch.object(main, "SHORT_DRAMA_RUNTIME", runtime), patch.object(
            main, "monitor_service", legacy
        ):
            await main.startup_monitor()
            await main.shutdown_monitor()
        legacy.auto_resume.assert_awaited_once()
        legacy.shutdown.assert_awaited_once()
