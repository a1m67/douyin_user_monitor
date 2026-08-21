from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from douyin_user_monitor import main


class MainLifecycleTests(IsolatedAsyncioTestCase):
    async def test_create_app_defers_runtime_build_until_lifespan(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(
                legacy_monitor_enabled=False,
                check_interval_minutes=10,
                admin_api_token="",
            ),
            repository=SimpleNamespace(),
            pipeline=SimpleNamespace(),
            dispatcher=SimpleNamespace(),
            scheduler=SimpleNamespace(),
            history_backfill_worker=SimpleNamespace(),
            cookie_manager=SimpleNamespace(),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )
        with patch.object(main, "build_short_drama_runtime", return_value=runtime) as build:
            application = main.create_app()
            build.assert_not_called()
            async with application.router.lifespan_context(application):
                self.assertIs(application.state.short_drama_runtime, runtime)
            build.assert_called_once_with()
        runtime.start.assert_awaited_once()
        runtime.shutdown.assert_awaited_once()

    async def test_app_exposes_legacy_mode_without_starting_it_by_default(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(legacy_monitor_enabled=False, check_interval_minutes=10, admin_api_token=""),
            repository=SimpleNamespace(), pipeline=SimpleNamespace(), dispatcher=SimpleNamespace(),
            scheduler=SimpleNamespace(), history_backfill_worker=SimpleNamespace(), cookie_manager=SimpleNamespace(),
            start=AsyncMock(), shutdown=AsyncMock(),
        )
        with patch.object(main.importlib, "import_module") as import_module:
            application = main.create_app(runtime)
        import_module.assert_not_called()
        self.assertFalse(application.state.legacy_monitor_enabled)

    async def test_default_does_not_start_or_stop_legacy_monitor(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(legacy_monitor_enabled=False),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )
        legacy = SimpleNamespace(monitor_service=SimpleNamespace(auto_resume=AsyncMock(), shutdown=AsyncMock()))
        with patch.object(main, "SHORT_DRAMA_RUNTIME", runtime), patch.object(
            main, "_legacy_monitor_module", return_value=legacy
        ):
            await main.startup_monitor()
            await main.shutdown_monitor()
        runtime.start.assert_awaited_once()
        runtime.shutdown.assert_awaited_once()
        legacy.monitor_service.auto_resume.assert_not_awaited()
        legacy.monitor_service.shutdown.assert_not_awaited()

    async def test_explicit_setting_keeps_legacy_lifecycle_available(self):
        runtime = SimpleNamespace(
            settings=SimpleNamespace(legacy_monitor_enabled=True),
            start=AsyncMock(),
            shutdown=AsyncMock(),
        )
        legacy = SimpleNamespace(monitor_service=SimpleNamespace(auto_resume=AsyncMock(), shutdown=AsyncMock()))
        with patch.object(main, "SHORT_DRAMA_RUNTIME", runtime), patch.object(
            main, "_legacy_monitor_module", return_value=legacy
        ):
            await main.startup_monitor()
            await main.shutdown_monitor()
        legacy.monitor_service.auto_resume.assert_awaited_once()
        legacy.monitor_service.shutdown.assert_awaited_once()
