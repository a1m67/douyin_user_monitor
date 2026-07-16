from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from douyin_user_monitor.settings import (
    CRAWLER_CONFIG_ENV,
    CookieLivenessSettings,
    CrawlerSettings,
    DEFAULT_AVATAR_TIMEOUT_SECONDS,
    DEFAULT_COOKIE_LIVENESS_ALERT_COOLDOWN_HOURS,
    DEFAULT_COOKIE_LIVENESS_ENABLED,
    DEFAULT_COOKIE_LIVENESS_INTERVAL_HOURS,
    DEFAULT_COOKIE_LIVENESS_MIN_SAMPLES,
    DEFAULT_COOKIE_LIVENESS_SAMPLE_USER_COUNT,
    DEFAULT_COOKIE_LIVENESS_STALE_DAYS,
    DEFAULT_CRAWLER_CONFIG_PATH,
    DEFAULT_DOWNLOAD_ROOT,
    DEFAULT_HERMES_WEIXIN_BIN,
    DEFAULT_HERMES_WEIXIN_ENABLED,
    DEFAULT_HERMES_WEIXIN_HOME,
    DEFAULT_HERMES_WEIXIN_SSH_HOST,
    DEFAULT_HERMES_WEIXIN_SSH_USER,
    DEFAULT_HERMES_WEIXIN_TARGET,
    DEFAULT_HERMES_WEIXIN_TIMEOUT_SECONDS,
    DEFAULT_STATE_PATH,
    DEFAULT_TG_API_BASE,
    DEFAULT_TG_ENABLED,
    DEFAULT_TG_TIMEOUT_SECONDS,
    DOWNLOAD_ROOT_ENV,
    HermesWeixinSettings,
    MonitorSettings,
    NotificationSettings,
    STATE_PATH_ENV,
    Settings,
    TG_API_BASE_ENV,
    TG_BOT_TOKEN_ENV,
    TG_CHAT_ID_ENV,
    TG_ENABLED_ENV,
    TG_TIMEOUT_ENV,
    TelegramSettings,
    _ensure_dict,
    _load_yaml,
    _parse_bool,
    _parse_positive_float,
    _parse_positive_int,
    _resolve_config_path,
    _resolve_path,
)

def load_settings() -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    raw = _load_yaml(_resolve_config_path(project_root))
    crawler_raw = _ensure_dict(raw.get("crawler", {}), "crawler")
    monitor_raw = _ensure_dict(raw.get("monitor", {}), "monitor")
    notification_raw = _ensure_dict(raw.get("notifications", {}), "notifications")
    telegram_raw = _ensure_dict(notification_raw.get("telegram", {}), "notifications.telegram")
    hermes_raw = _ensure_dict(notification_raw.get("hermes_weixin", {}), "notifications.hermes_weixin")
    cookie_raw = _ensure_dict(raw.get("cookie_liveness", {}), "cookie_liveness")
    return Settings(
        project_root=project_root,
        crawler=_load_crawler_settings(project_root, crawler_raw),
        monitor=_load_monitor_settings(project_root, monitor_raw),
        notifications=NotificationSettings(
            telegram=_load_telegram_settings(telegram_raw),
            hermes_weixin=_load_hermes_settings(hermes_raw),
        ),
        cookie_liveness=_load_cookie_liveness_settings(cookie_raw),
    )


def _load_crawler_settings(project_root: Path, crawler_raw: Dict[str, Any]) -> CrawlerSettings:
    crawler_config_raw = os.getenv(CRAWLER_CONFIG_ENV, "") or str(
        crawler_raw.get("config_path", DEFAULT_CRAWLER_CONFIG_PATH)
    ).strip()
    timeout_raw = str(
        crawler_raw.get("timeout_seconds", DEFAULT_AVATAR_TIMEOUT_SECONDS)
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
    return CrawlerSettings(config_path=crawler_config_path, timeout_seconds=timeout_seconds)


def _load_monitor_settings(project_root: Path, monitor_raw: Dict[str, Any]) -> MonitorSettings:
    state_path_raw = os.getenv(STATE_PATH_ENV, "") or str(
        monitor_raw.get("state_path", DEFAULT_STATE_PATH)
    ).strip()
    download_root_raw = os.getenv(DOWNLOAD_ROOT_ENV, "") or str(
        monitor_raw.get("download_root", DEFAULT_DOWNLOAD_ROOT)
    ).strip()
    if not state_path_raw:
        raise ValueError(f"缺少状态文件路径：请设置 {STATE_PATH_ENV} 或 config.yaml 的 monitor.state_path")
    if not download_root_raw:
        raise ValueError(
            f"缺少下载目录：请设置 {DOWNLOAD_ROOT_ENV} 或 config.yaml 的 monitor.download_root"
        )
    return MonitorSettings(
        state_path=_resolve_path(project_root, state_path_raw),
        download_root=_resolve_path(project_root, download_root_raw),
    )


def _load_telegram_settings(telegram_raw: Dict[str, Any]) -> TelegramSettings:
    tg_enabled_raw = os.getenv(TG_ENABLED_ENV, "") or telegram_raw.get("enabled", DEFAULT_TG_ENABLED)
    tg_bot_token = os.getenv(TG_BOT_TOKEN_ENV, "") or str(telegram_raw.get("bot_token", "")).strip()
    tg_chat_id = os.getenv(TG_CHAT_ID_ENV, "") or str(telegram_raw.get("chat_id", "")).strip()
    tg_api_base = os.getenv(TG_API_BASE_ENV, "") or str(
        telegram_raw.get("api_base", DEFAULT_TG_API_BASE)
    ).strip()
    tg_timeout_raw = os.getenv(TG_TIMEOUT_ENV, "") or str(
        telegram_raw.get("timeout_seconds", DEFAULT_TG_TIMEOUT_SECONDS)
    ).strip()
    tg_enabled = _parse_bool(tg_enabled_raw)
    tg_timeout_seconds = _parse_telegram_timeout(tg_timeout_raw)
    if tg_enabled:
        _validate_telegram_enabled(tg_bot_token, tg_chat_id, tg_timeout_seconds)
    return TelegramSettings(
        enabled=tg_enabled,
        bot_token=tg_bot_token,
        chat_id=tg_chat_id,
        api_base=tg_api_base,
        timeout_seconds=tg_timeout_seconds,
    )


def _parse_telegram_timeout(tg_timeout_raw: str) -> float:
    try:
        return float(tg_timeout_raw)
    except ValueError as exc:
        raise ValueError("notifications.telegram.timeout_seconds 必须是数字") from exc


def _validate_telegram_enabled(bot_token: str, chat_id: str, timeout_seconds: float) -> None:
    if not bot_token:
        raise ValueError(
            f"启用 Telegram 通知时必须设置 {TG_BOT_TOKEN_ENV} 或 notifications.telegram.bot_token"
        )
    if not chat_id:
        raise ValueError(
            f"启用 Telegram 通知时必须设置 {TG_CHAT_ID_ENV} 或 notifications.telegram.chat_id"
        )
    if timeout_seconds <= 0:
        raise ValueError("notifications.telegram.timeout_seconds 必须大于 0")


def _load_hermes_settings(hermes_raw: Dict[str, Any]) -> HermesWeixinSettings:
    hermes_enabled = _parse_bool(hermes_raw.get("enabled", DEFAULT_HERMES_WEIXIN_ENABLED))
    hermes_host = str(hermes_raw.get("ssh_host", DEFAULT_HERMES_WEIXIN_SSH_HOST)).strip()
    hermes_user = str(hermes_raw.get("ssh_user", DEFAULT_HERMES_WEIXIN_SSH_USER)).strip()
    hermes_home = str(hermes_raw.get("hermes_home", DEFAULT_HERMES_WEIXIN_HOME)).strip()
    hermes_bin = str(hermes_raw.get("hermes_bin", DEFAULT_HERMES_WEIXIN_BIN)).strip()
    hermes_target = str(hermes_raw.get("target", DEFAULT_HERMES_WEIXIN_TARGET)).strip()
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
    return HermesWeixinSettings(
        enabled=hermes_enabled,
        ssh_host=hermes_host,
        ssh_user=hermes_user,
        hermes_home=hermes_home,
        hermes_bin=hermes_bin,
        target=hermes_target,
        timeout_seconds=hermes_timeout_seconds,
    )


def _load_cookie_liveness_settings(cookie_raw: Dict[str, Any]) -> CookieLivenessSettings:
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
    return CookieLivenessSettings(
        enabled=cookie_enabled,
        interval_hours=cookie_interval,
        stale_days=cookie_stale_days,
        sample_user_count=cookie_sample_count,
        min_samples=cookie_min_samples,
        alert_cooldown_hours=cookie_cooldown,
    )


