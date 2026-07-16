"""Load Douyin web crawler TokenManager config from an injectable path."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_ENV_KEY = "DYMON_CRAWLER_CONFIG"
_config: Optional[Dict[str, Any]] = None
_config_path: Optional[Path] = None


def set_config_path(path: str | Path) -> None:
    global _config, _config_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"crawler config not found: {resolved}")
    _config_path = resolved
    os.environ[_ENV_KEY] = str(resolved)
    _config = None


def get_config_path() -> Path:
    if _config_path is not None:
        return _config_path
    env = os.environ.get(_ENV_KEY, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent / "config.yaml"


def load_config(*, force_reload: bool = False) -> Dict[str, Any]:
    global _config, _config_path
    if _config is not None and not force_reload:
        return _config
    path = get_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"crawler config not found: {path}. "
            f"Set crawler.config_path or {_ENV_KEY}."
        )
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"crawler config must be a mapping: {path}")
    _config = data
    _config_path = path
    return data


def get_config() -> Dict[str, Any]:
    return load_config()
