"""Single-user signed-cookie authentication for the dashboard."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


SESSION_COOKIE = "short_drama_session"
CSRF_COOKIE = "short_drama_csrf"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_PATHS = {"/health", "/login", "/manifest.webmanifest", "/pwa-icon.svg", "/sw.js", "/version"}


@dataclass(frozen=True)
class AppAuthConfig:
    enabled: bool = False
    password: str = ""
    session_secret: str = ""
    session_ttl_hours: int = 168
    cookie_secure: str = "auto"
    admin_api_token: str = ""


class SignedSessionManager:
    def __init__(self, secret: str, ttl_hours: int) -> None:
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_hours * 3600

    def issue(self, *, now: int | None = None) -> tuple[str, str]:
        issued_at = int(now if now is not None else time.time())
        csrf_token = secrets.token_urlsafe(32)
        payload = {
            "issued_at": issued_at,
            "expires_at": issued_at + self._ttl_seconds,
            "csrf": csrf_token,
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", csrf_token

    def verify(self, value: str | None, *, now: int | None = None) -> dict[str, object] | None:
        if not value or "." not in value:
            return None
        encoded, supplied_signature = value.split(".", 1)
        expected = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not secrets.compare_digest(supplied_signature, expected):
            return None
        try:
            payload = json.loads(_b64decode(encoded))
            current = int(now if now is not None else time.time())
            if int(payload["issued_at"]) > current + 60 or int(payload["expires_at"]) <= current:
                return None
            if not isinstance(payload.get("csrf"), str) or not payload["csrf"]:
                return None
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        return payload


class AppAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, config: AppAuthConfig) -> None:
        super().__init__(app)
        self.config = config
        self.sessions = SignedSessionManager(config.session_secret, config.session_ttl_hours)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.config.enabled:
            return _security_headers(await call_next(request))

        path = request.url.path
        session = self.sessions.verify(request.cookies.get(SESSION_COOKIE))
        request.state.app_session = session
        request.state.auth_mode = None

        if path == "/login" or path.startswith("/static/") or path in PUBLIC_PATHS:
            return _security_headers(await call_next(request))

        bearer_valid = self._valid_bearer(request)
        is_short_drama_api = path.startswith("/api/short-drama/")
        if is_short_drama_api:
            if bearer_valid:
                request.state.auth_mode = "bearer"
            elif session is not None:
                request.state.auth_mode = "session"
                denied = self._validate_browser_write(request, session)
                if denied is not None:
                    return _security_headers(denied)
            else:
                return _security_headers(JSONResponse({"detail": "需要登录"}, status_code=401))
            return _security_headers(await call_next(request))

        if path == "/logout":
            if session is None:
                return _security_headers(RedirectResponse("/login", status_code=303))
            denied = self._validate_browser_write(request, session)
            if denied is not None:
                return _security_headers(denied)
            return _security_headers(await call_next(request))

        if _is_dashboard_path(path):
            if session is None:
                return _security_headers(RedirectResponse("/login", status_code=303))
            request.state.auth_mode = "session"
        return _security_headers(await call_next(request))

    def _valid_bearer(self, request: Request) -> bool:
        token = self.config.admin_api_token
        if not token:
            return False
        scheme, separator, credential = request.headers.get("authorization", "").partition(" ")
        return bool(separator and scheme.casefold() == "bearer" and secrets.compare_digest(credential, token))

    def _validate_browser_write(self, request: Request, session: dict[str, object]) -> Response | None:
        if request.method not in MUTATING_METHODS:
            return None
        origin = request.headers.get("origin")
        if origin and not _same_origin(request, origin):
            return JSONResponse({"detail": "请求 Origin 无效"}, status_code=403)
        expected = str(session["csrf"])
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get("x-csrf-token", "")
        if not cookie_token or not header_token:
            return JSONResponse({"detail": "缺少 CSRF token"}, status_code=403)
        if not secrets.compare_digest(cookie_token, expected) or not secrets.compare_digest(header_token, expected):
            return JSONResponse({"detail": "CSRF token 无效"}, status_code=403)
        return None


def create_auth_router(config: AppAuthConfig) -> APIRouter:
    router = APIRouter()
    sessions = SignedSessionManager(config.session_secret, config.session_ttl_hours)

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request) -> Response:
        if not config.enabled:
            return RedirectResponse("/following", status_code=303)
        if sessions.verify(request.cookies.get(SESSION_COOKIE)) is not None:
            return RedirectResponse("/following", status_code=303)
        return _login_html()

    @router.post("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(request: Request) -> Response:
        if not config.enabled:
            return RedirectResponse("/following", status_code=303)
        origin = request.headers.get("origin")
        if origin and not _same_origin(request, origin):
            return JSONResponse({"detail": "请求 Origin 无效"}, status_code=403)
        body = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        password = str(body.get("password", [""])[0])
        if not secrets.compare_digest(password, config.password):
            return _login_html(error="密码错误", status_code=401)
        session_value, csrf_token = sessions.issue()
        response = RedirectResponse("/following", status_code=303)
        secure = _secure_cookie(request, config.cookie_secure)
        response.set_cookie(SESSION_COOKIE, session_value, httponly=True, secure=secure, samesite="strict", path="/", max_age=config.session_ttl_hours * 3600)
        response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, secure=secure, samesite="strict", path="/", max_age=config.session_ttl_hours * 3600)
        return response

    @router.post("/logout", include_in_schema=False)
    async def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    return router


def _login_html(*, error: str = "", status_code: int = 200) -> HTMLResponse:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · AI 短剧追更</title><link rel="stylesheet" href="/static/app.css"></head><body class="login-body"><main class="login-panel"><h1>AI 短剧追更</h1><p>请输入管理密码继续。</p>{error_html}<form method="post" action="/login"><label class="field">密码<input name="password" type="password" autocomplete="current-password" required autofocus></label><button type="submit">登录</button></form></main></body></html>""",
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _is_dashboard_path(path: str) -> bool:
    return path == "/" or path in {"/following", "/updates", "/shows", "/accounts", "/videos", "/review", "/status", "/quality", "/diagnostics", "/settings/crawler"} or path.startswith("/shows/")


def _same_origin(request: Request, origin: str) -> bool:
    parsed = urlparse(origin)
    expected_host = request.headers.get("host", "")
    expected_scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
    return parsed.scheme == expected_scheme and parsed.netloc.casefold() == expected_host.casefold()


def _secure_cookie(request: Request, setting: str) -> bool:
    if setting == "true":
        return True
    if setting == "false":
        return False
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip() == "https"


def _security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
