from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_ENV = "DYMON_CONFIG"
CRAWLER_CONFIG_ENV = "DYMON_CRAWLER_CONFIG"
STATE_PATH_ENV = "DYMON_STATE_PATH"
DOWNLOAD_ROOT_ENV = "DYMON_DOWNLOAD_ROOT"
TG_ENABLED_ENV = "DYMON_TG_ENABLED"
TG_BOT_TOKEN_ENV = "DYMON_TG_BOT_TOKEN"
TG_CHAT_ID_ENV = "DYMON_TG_CHAT_ID"
TG_API_BASE_ENV = "DYMON_TG_API_BASE"
TG_TIMEOUT_ENV = "DYMON_TG_TIMEOUT"

DEFAULT_CRAWLER_CONFIG_PATH = "config/douyin_web.yaml"
DEFAULT_AVATAR_TIMEOUT_SECONDS = 30.0
DEFAULT_STATE_PATH = "data/monitor_users.json"
DEFAULT_DOWNLOAD_ROOT = "download"
DEFAULT_TG_ENABLED = False
DEFAULT_TG_API_BASE = "https://api.telegram.org"
DEFAULT_TG_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class CrawlerSettings:
    config_path: Path
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
class HermesWeixinSettings:
    enabled: bool
    ssh_host: str
    ssh_user: str
    hermes_home: str
    hermes_bin: str
    target: str
    timeout_seconds: float


@dataclass(frozen=True)
class NotificationSettings:
    telegram: TelegramSettings
    hermes_weixin: HermesWeixinSettings


@dataclass(frozen=True)
class CookieLivenessSettings:
    enabled: bool
    interval_hours: float
    stale_days: float
    sample_user_count: int
    min_samples: int
    alert_cooldown_hours: float


@dataclass(frozen=True)
class Settings:
    project_root: Path
    crawler: CrawlerSettings
    monitor: MonitorSettings
    notifications: NotificationSettings
    cookie_liveness: CookieLivenessSettings


DEFAULT_COOKIE_LIVENESS_ENABLED = True
DEFAULT_COOKIE_LIVENESS_INTERVAL_HOURS = 6.0
DEFAULT_COOKIE_LIVENESS_STALE_DAYS = 7.0
DEFAULT_COOKIE_LIVENESS_SAMPLE_USER_COUNT = 5
DEFAULT_COOKIE_LIVENESS_MIN_SAMPLES = 3
DEFAULT_COOKIE_LIVENESS_ALERT_COOLDOWN_HOURS = 12.0
DEFAULT_HERMES_WEIXIN_ENABLED = False
DEFAULT_HERMES_WEIXIN_SSH_HOST = "hermes.example.test"
DEFAULT_HERMES_WEIXIN_SSH_USER = "root"
DEFAULT_HERMES_WEIXIN_HOME = "/opt/hermes"
DEFAULT_HERMES_WEIXIN_BIN = "/opt/hermes/bin/hermes"
DEFAULT_HERMES_WEIXIN_TARGET = "weixin"
DEFAULT_HERMES_WEIXIN_TIMEOUT_SECONDS = 60.0


def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    raw = _load_yaml(_resolve_config_path(project_root))
    crawler_raw = _ensure_dict(raw.get("crawler", {}), "crawler")
    monitor_raw = _ensure_dict(raw.get("monitor", {}), "monitor")
    notification_raw = _ensure_dict(raw.get("notifications", {}), "notifications")
    telegram_raw = _ensure_dict(notification_raw.get("telegram", {}), "notifications.telegram")
    hermes_raw = _ensure_dict(notification_raw.get("hermes_weixin", {}), "notifications.hermes_weixin")
    cookie_raw = _ensure_dict(raw.get("cookie_liveness", {}), "cookie_liveness")

    crawler_config_raw = os.getenv(CRAWLER_CONFIG_ENV, "") or str(
        crawler_raw.get("config_path", DEFAULT_CRAWLER_CONFIG_PATH)
    ).strip()
    timeout_raw = str(
        crawler_raw.get("timeout_seconds", DEFAULT_AVATAR_TIMEOUT_SECONDS)
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

    if not crawler_config_raw:
        raise ValueError(
            f"缺少爬虫配置路径：请设置 {CRAWLER_CONFIG_ENV} 或 config.yaml 的 crawler.config_path"
        )

    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise ValueError("crawler.timeout_seconds 必须是数字") from exc
    if timeout_seconds <= 0:
        raise ValueError("crawler.timeout_seconds 必须大于 0")

    crawler_config_path = _resolve_path(project_root, crawler_config_raw)
    if not crawler_config_path.is_file():
        raise ValueError(
            f"爬虫配置文件不存在: {crawler_config_path}。"
            "请从 config/douyin_web.example.yaml 复制并填入 Cookie。"
        )

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

    hermes_enabled = _parse_bool(
        hermes_raw.get("enabled", DEFAULT_HERMES_WEIXIN_ENABLED)
    )
    hermes_host = str(hermes_raw.get("ssh_host", DEFAULT_HERMES_WEIXIN_SSH_HOST)).strip()
    hermes_user = str(hermes_raw.get("ssh_user", DEFAULT_HERMES_WEIXIN_SSH_USER)).strip() or "root"
    hermes_home = str(hermes_raw.get("hermes_home", DEFAULT_HERMES_WEIXIN_HOME)).strip()
    hermes_bin = str(hermes_raw.get("hermes_bin", DEFAULT_HERMES_WEIXIN_BIN)).strip()
    hermes_target = str(hermes_raw.get("target", DEFAULT_HERMES_WEIXIN_TARGET)).strip() or "weixin"
    hermes_timeout_raw = str(
        hermes_raw.get("timeout_seconds", DEFAULT_HERMES_WEIXIN_TIMEOUT_SECONDS)
    ).strip()
    try:
        hermes_timeout_seconds = float(hermes_timeout_raw)
    except ValueError as exc:
        raise ValueError("notifications.hermes_weixin.timeout_seconds 必须是数字") from exc
    if hermes_enabled:
        if not hermes_host:
            raise ValueError("启用 hermes 微信通知时必须设置 notifications.hermes_weixin.ssh_host")
        if not hermes_home:
            raise ValueError("启用 hermes 微信通知时必须设置 notifications.hermes_weixin.hermes_home")
        if not hermes_bin:
            raise ValueError("启用 hermes 微信通知时必须设置 notifications.hermes_weixin.hermes_bin")
        if hermes_timeout_seconds <= 0:
            raise ValueError("notifications.hermes_weixin.timeout_seconds 必须大于 0")

    cookie_enabled = _parse_bool(cookie_raw.get("enabled", DEFAULT_COOKIE_LIVENESS_ENABLED))
    cookie_interval = _parse_positive_float(
        cookie_raw.get("interval_hours", DEFAULT_COOKIE_LIVENESS_INTERVAL_HOURS),
        "cookie_liveness.interval_hours",
    )
    cookie_stale_days = _parse_positive_float(
        cookie_raw.get("stale_days", DEFAULT_COOKIE_LIVENESS_STALE_DAYS),
        "cookie_liveness.stale_days",
    )
    cookie_sample_count = _parse_positive_int(
        cookie_raw.get("sample_user_count", DEFAULT_COOKIE_LIVENESS_SAMPLE_USER_COUNT),
        "cookie_liveness.sample_user_count",
    )
    cookie_min_samples = _parse_positive_int(
        cookie_raw.get("min_samples", DEFAULT_COOKIE_LIVENESS_MIN_SAMPLES),
        "cookie_liveness.min_samples",
    )
    cookie_cooldown = _parse_positive_float(
        cookie_raw.get("alert_cooldown_hours", DEFAULT_COOKIE_LIVENESS_ALERT_COOLDOWN_HOURS),
        "cookie_liveness.alert_cooldown_hours",
    )
    if cookie_min_samples > cookie_sample_count:
        raise ValueError("cookie_liveness.min_samples 不能大于 sample_user_count")

    return Settings(
        project_root=project_root,
        crawler=CrawlerSettings(config_path=crawler_config_path, timeout_seconds=timeout_seconds),
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
            ),
            hermes_weixin=HermesWeixinSettings(
                enabled=hermes_enabled,
                ssh_host=hermes_host,
                ssh_user=hermes_user,
                hermes_home=hermes_home,
                hermes_bin=hermes_bin,
                target=hermes_target,
                timeout_seconds=hermes_timeout_seconds,
            ),
        ),
        cookie_liveness=CookieLivenessSettings(
            enabled=cookie_enabled,
            interval_hours=cookie_interval,
            stale_days=cookie_stale_days,
            sample_user_count=cookie_sample_count,
            min_samples=cookie_min_samples,
            alert_cooldown_hours=cookie_cooldown,
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


def _parse_positive_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if number <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return number


def _parse_positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if number <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return number
