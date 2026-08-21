from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from douyin_user_monitor.web.auth import AppAuthConfig, AppAuthMiddleware, SignedSessionManager, create_auth_router


class SignedSessionTests(TestCase):
    def test_signed_session_round_trip_and_tamper_expiry(self):
        manager = SignedSessionManager("x" * 32, 1)
        value, csrf = manager.issue(now=100)
        payload = manager.verify(value, now=100)
        self.assertEqual(payload["csrf"], csrf)
        self.assertIsNone(manager.verify(value + "x", now=100))
        self.assertIsNone(manager.verify(value, now=3701))


class WebAuthTests(TestCase):
    def _app(self) -> FastAPI:
        app = FastAPI()
        config = AppAuthConfig(enabled=True, password="pw", session_secret="s" * 32, admin_api_token="admin")
        app.add_middleware(AppAuthMiddleware, config=config)
        app.include_router(create_auth_router(config))

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/following")
        async def following():
            return {"page": "following"}

        @app.get("/media/accounts/example/avatar")
        async def avatar():
            return {"image": True}

        @app.post("/api/short-drama/write")
        async def write():
            return {"ok": True}

        return app

    def test_login_protects_page_and_csrf_protects_session_writes(self):
        client = TestClient(self._app())
        self.assertEqual(client.get("/following", follow_redirects=False).status_code, 303)
        self.assertEqual(client.get("/media/accounts/example/avatar").status_code, 401)
        self.assertEqual(client.get("/login").status_code, 200)
        response = client.post("/login", data={"password": "pw"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(client.get("/following").status_code, 200)
        self.assertEqual(client.get("/media/accounts/example/avatar").status_code, 200)
        self.assertEqual(client.post("/api/short-drama/write").status_code, 403)
        csrf = client.cookies.get("short_drama_csrf")
        allowed = client.post("/api/short-drama/write", headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"})
        self.assertEqual(allowed.status_code, 200)

    def test_bearer_token_bypasses_session_and_headers_are_added(self):
        client = TestClient(self._app())
        response = client.post("/api/short-drama/write", headers={"Authorization": "Bearer admin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/health").headers["x-frame-options"], "DENY")
