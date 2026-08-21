"""Server-rendered shell, PWA, and static asset routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from douyin_user_monitor.web.build_info import WebBuildInfo, web_build_info


_PAGE_ROUTES = (
    "/shows",
    "/following",
    "/updates",
    "/accounts",
    "/videos",
    "/review",
    "/status",
    "/settings/crawler",
    "/diagnostics",
    "/quality",
)


def create_page_router(
    *,
    page_path: Path | None = None,
    default_check_interval_minutes: int = 10,
    build_info: WebBuildInfo | None = None,
) -> APIRouter:
    router = APIRouter()
    html_path = page_path or Path(__file__).with_name("short_drama.html")
    asset_dir = html_path.parent
    build = build_info or web_build_info(asset_dir)

    def page() -> HTMLResponse:
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="短剧 Dashboard 文件不存在")
        html = html_path.read_text(encoding="utf-8").replace(
                "{{DEFAULT_CHECK_INTERVAL_MINUTES}}",
                str(default_check_interval_minutes),
            ).replace("{{BUILD_ID}}", build.build_id)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    for route_path in _PAGE_ROUTES:
        router.add_api_route(
            route_path,
            page,
            methods=["GET"],
            response_class=HTMLResponse,
            include_in_schema=False,
        )
    router.add_api_route(
        "/shows/{show_id}",
        page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )

    @router.get("/manifest.webmanifest", include_in_schema=False)
    async def pwa_manifest() -> Response:
        return Response(
            (asset_dir / "manifest.webmanifest").read_text(encoding="utf-8"),
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @router.get("/sw.js", include_in_schema=False)
    async def service_worker() -> Response:
        return Response(
            (asset_dir / "sw.js").read_text(encoding="utf-8").replace("{{BUILD_ID}}", build.build_id),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @router.get("/pwa-icon.svg", include_in_schema=False)
    async def pwa_icon() -> Response:
        return Response(
            (asset_dir / "pwa-icon.svg").read_text(encoding="utf-8"),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @router.get("/static/{asset_name}", include_in_schema=False)
    async def static_asset(asset_name: str, v: str | None = Query(default=None)) -> Response:
        media_types = {
            "app.css": "text/css",
            "api.js": "application/javascript",
            "core.js": "application/javascript",
            "shows.js": "application/javascript",
            "library.js": "application/javascript",
            "system.js": "application/javascript",
            "app.js": "application/javascript",
        }
        if asset_name not in media_types:
            raise HTTPException(status_code=404, detail="静态资源不存在")
        return Response(
            (asset_dir / "static" / asset_name).read_text(encoding="utf-8"),
            media_type=media_types[asset_name],
            headers={
                "Cache-Control": (
                    "public, max-age=31536000, immutable"
                    if v == build.build_id else "no-cache"
                )
            },
        )

    @router.get("/version", include_in_schema=False)
    async def version() -> dict[str, str]:
        return {"app_version": build.app_version, "build_id": build.build_id}

    return router
