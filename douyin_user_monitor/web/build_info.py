"""Deterministic, non-sensitive identity for the deployed web asset set."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from douyin_user_monitor import __version__


@dataclass(frozen=True)
class WebBuildInfo:
    app_version: str
    build_id: str


def web_build_info(asset_dir: Path | None = None) -> WebBuildInfo:
    root = asset_dir or Path(__file__).resolve().parent
    names = (
        "short_drama.html", "manifest.webmanifest", "sw.js", "pwa-icon.svg",
        "static/app.css", "static/api.js", "static/core.js", "static/shows.js",
        "static/library.js", "static/system.js", "static/app.js",
    )
    digest = hashlib.sha256()
    for name in names:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return WebBuildInfo(app_version=__version__, build_id=digest.hexdigest()[:16])
