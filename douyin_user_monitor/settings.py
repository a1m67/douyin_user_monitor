from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_ENV = "DYMON_CONFIG"
UPSTREAM_ENV = "DYMON_UPSTREAM"
UPSTREAM_TIMEOUT_ENV = "DYMON_UPSTREAM_TIMEOUT"
STATE_PATH_ENV = "DYMON_STATE_PATH"
DOWNLOAD_ROOT_ENV = "DYMON_DOWNLOAD_ROOT"
TG_ENABLED_ENV = "DYMON_TG_ENABLED"
TG_BOT_TOKEN_ENV = "DYMON_TG_BOT_TOKEN"
TG_CHAT_ID_ENV = "DYMON_TG_CHAT_ID"
TG_API_BASE_ENV = "DYMON_TG_API_BASE"
TG_TIMEOUT_ENV = "DYMON_TG_TIMEOUT"

DEFAULT_UPSTREAM_BASE_URL = "http://127.0.0.1:8899"
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = 30.0
DEFAULT_STATE_PATH = "data/monitor_users.json"
DEFAULT_DOWNLOAD_ROOT = "download"
DEFAULT_TG_ENABLED = False
DEFAULT_TG_API_BASE = "https://api.telegram.org"
DEFAULT_TG_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class UpstreamSettings:
    base_url: str
    timeout_seconds: float


@dataclass(frozen=True)
class MonitorSettings:
    state_path: Path
    download_root: Path


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool
    bot_token: str
    chat_id: str
    api_base: str
    timeout_seconds: float


@dataclass(frozen=True)
class NotificationSettings:
    telegram: TelegramSettings


@dataclass(frozen=True)
class Settings:
    project_root: Path
    upstream: UpstreamSettings
    monitor: MonitorSettings
    notifications: NotificationSettings


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    raw = _load_yaml(_resolve_config_path(project_root))
    upstream_raw = _ensure_dict(raw.get("upstream", {}), "upstream")
    monitor_raw = _ensure_dict(raw.get("monitor", {}), "monitor")
    notification_raw = _ensure_dict(raw.get("notifications", {}), "notifications")
    telegram_raw = _ensure_dict(notification_raw.get("telegram", {}), "notifications.telegram")

    base_url = os.getenv(UPSTREAM_ENV, "") or str(
        upstream_raw.get("base_url", DEFAULT_UPSTREAM_BASE_URL)
    ).strip()
    timeout_raw = os.getenv(UPSTREAM_TIMEOUT_ENV, "") or str(
        upstream_raw.get("timeout_seconds", DEFAULT_UPSTREAM_TIMEOUT_SECONDS)
    ).strip()

    state_path_raw = os.getenv(STATE_PATH_ENV, "") or str(
        monitor_raw.get("state_path", DEFAULT_STATE_PATH)
    ).strip()
    download_root_raw = os.getenv(DOWNLOAD_ROOT_ENV, "") or str(
        monitor_raw.get("download_root", DEFAULT_DOWNLOAD_ROOT)
    ).strip()
    tg_enabled_raw = os.getenv(TG_ENABLED_ENV, "") or telegram_raw.get("enabled", DEFAULT_TG_ENABLED)
    tg_bot_token = os.getenv(TG_BOT_TOKEN_ENV, "") or str(telegram_raw.get("bot_token", "")).strip()
    tg_chat_id = os.getenv(TG_CHAT_ID_ENV, "") or str(telegram_raw.get("chat_id", "")).strip()
    tg_api_base = os.getenv(TG_API_BASE_ENV, "") or str(
        telegram_raw.get("api_base", DEFAULT_TG_API_BASE)
    ).strip()
    tg_timeout_raw = os.getenv(TG_TIMEOUT_ENV, "") or str(
        telegram_raw.get("timeout_seconds", DEFAULT_TG_TIMEOUT_SECONDS)
    ).strip()

    if not base_url:
        raise ValueError(f"缺少上游服务地址：请设置 {UPSTREAM_ENV} 或 config.yaml 的 upstream.base_url")

    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError("upstream.timeout_seconds 必须是数字") from exc
    if timeout_seconds <= 0:
        raise ValueError("upstream.timeout_seconds 必须大于 0")

    if not state_path_raw:
        raise ValueError(f"缺少状态文件路径：请设置 {STATE_PATH_ENV} 或 config.yaml 的 monitor.state_path")
    if not download_root_raw:
        raise ValueError(f"缺少下载目录：请设置 {DOWNLOAD_ROOT_ENV} 或 config.yaml 的 monitor.download_root")
    tg_enabled = _parse_bool(tg_enabled_raw)
    if tg_enabled and not tg_bot_token:
        raise ValueError(f"启用 Telegram 通知时必须设置 {TG_BOT_TOKEN_ENV} 或 notifications.telegram.bot_token")
    if tg_enabled and not tg_chat_id:
        raise ValueError(f"启用 Telegram 通知时必须设置 {TG_CHAT_ID_ENV} 或 notifications.telegram.chat_id")
    if not tg_api_base:
        raise ValueError("notifications.telegram.api_base 不能为空")
    try:
        tg_timeout_seconds = float(tg_timeout_raw)
    except ValueError as exc:
        raise ValueError("notifications.telegram.timeout_seconds 必须是数字") from exc
    if tg_timeout_seconds <= 0:
        raise ValueError("notifications.telegram.timeout_seconds 必须大于 0")

    return Settings(
        project_root=project_root,
        upstream=UpstreamSettings(base_url=base_url, timeout_seconds=timeout_seconds),
        monitor=MonitorSettings(
            state_path=_resolve_path(project_root, state_path_raw),
            download_root=_resolve_path(project_root, download_root_raw),
        ),
        notifications=NotificationSettings(
            telegram=TelegramSettings(
                enabled=tg_enabled,
                bot_token=tg_bot_token,
                chat_id=tg_chat_id,
                api_base=tg_api_base,
                timeout_seconds=tg_timeout_seconds,
            )
        ),
    )


def _resolve_config_path(project_root: Path) -> Path:
    path_value = os.getenv(CONFIG_ENV, "")
    if path_value:
        return Path(path_value).expanduser()
    return project_root / "config.yaml"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _ensure_dict(data or {}, str(path))


def _ensure_dict(value: Any, name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"配置项 {name} 必须是对象(dict)")
    return value


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"布尔配置值无效: {value}")
