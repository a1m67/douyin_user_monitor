"""Server-rendered shell, PWA, and static asset routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response


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
) -> APIRouter:
    router = APIRouter()
    html_path = page_path or Path(__file__).with_name("short_drama.html")
    asset_dir = html_path.parent

    def page() -> HTMLResponse:
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="短剧 Dashboard 文件不存在")
        return HTMLResponse(
            html_path.read_text(encoding="utf-8").replace(
                "{{DEFAULT_CHECK_INTERVAL_MINUTES}}",
                str(default_check_interval_minutes),
            ),
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
        )

    @router.get("/sw.js", include_in_schema=False)
    async def service_worker() -> Response:
        return Response(
            (asset_dir / "sw.js").read_text(encoding="utf-8"),
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
    async def static_asset(asset_name: str) -> Response:
        media_types = {
            "app.css": "text/css",
            "api.js": "application/javascript",
            "app.js": "application/javascript",
        }
        if asset_name not in media_types:
            raise HTTPException(status_code=404, detail="静态资源不存在")
        return Response(
            (asset_dir / "static" / asset_name).read_text(encoding="utf-8"),
            media_type=media_types[asset_name],
            headers={"Cache-Control": "no-cache"},
        )

    return router
