"""Environment-first settings for the AI short-drama tracker."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ShortDramaSettings:
    project_root: Path
    database_url: str
    database_path: Path
    crawler_config_path: Path
    cookie_file: Path
    check_interval_minutes: int
    max_concurrent_checks: int
    initial_sync_limit: int
    incremental_fetch_limit: int
    history_backfill_page_size: int
    history_backfill_delay_min_seconds: float
    history_backfill_delay_max_seconds: float
    max_concurrent_history_backfills: int
    notify_on_initial_sync: bool
    auto_accept_confidence: float
    llm_enabled: bool
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: float
    llm_auto_accept_confidence: float
    max_backoff_minutes: int
    scheduler_poll_seconds: float
    adaptive_scheduler_enabled: bool
    adaptive_min_interval_minutes: int
    adaptive_max_interval_minutes: int
    telegram_bot_token: str
    telegram_chat_id: str
    feishu_webhook_url: str
    notification_poll_seconds: float
    notification_max_attempts: int
    notification_max_backoff_seconds: int
    notification_claim_timeout_seconds: int
    admin_api_token: str
    app_auth_enabled: bool
    app_auth_password: str
    app_session_secret: str
    app_session_ttl_hours: int
    app_cookie_secure: str
    crawler_circuit_breaker_enabled: bool
    crawler_circuit_failure_threshold: int
    crawler_circuit_open_minutes: int
    douyin_max_concurrent_requests: int
    douyin_min_request_interval_seconds: float
    legacy_monitor_enabled: bool
    scan_run_retention_days: int
    backup_retention_count: int
    auto_maintenance_enabled: bool
    auto_backup_interval_hours: int
    maintenance_poll_seconds: float
    wal_checkpoint_interval_hours: int
    raw_json_prune_batch_size: int
    ocr_enabled: bool
    ocr_timeout_seconds: float
    ocr_api_url: str
    ocr_api_key: str


def load_short_drama_settings(
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> ShortDramaSettings:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    values = dict(_load_dotenv(root / ".env"))
    values.update({key: value for key, value in (environ or os.environ).items() if value is not None})

    database_url = _value(values, "DATABASE_URL", "sqlite:///data/app.db")
    database_path = _database_path(root, database_url)
    crawler_config = _path(
        root,
        _value(values, "DYMON_CRAWLER_CONFIG", "config/douyin_web.yaml"),
    )
    if not crawler_config.is_file():
        crawler_config = root / "config" / "douyin_web.example.yaml"
    if not crawler_config.is_file():
        raise ValueError(f"找不到抖音 crawler 配置文件: {crawler_config}")

    settings = ShortDramaSettings(
        project_root=root,
        database_url=database_url,
        database_path=database_path,
        crawler_config_path=crawler_config,
        cookie_file=_path(root, _value(values, "DOUYIN_COOKIE_FILE", "data/cookies.json")),
        check_interval_minutes=_positive_int(values, "CHECK_INTERVAL_MINUTES", 10),
        max_concurrent_checks=_positive_int(values, "MAX_CONCURRENT_CHECKS", 3),
        initial_sync_limit=_positive_int(values, "INITIAL_SYNC_LIMIT", 20),
        incremental_fetch_limit=_positive_int(values, "INCREMENTAL_FETCH_LIMIT", 30),
        history_backfill_page_size=_positive_int(values, "HISTORY_BACKFILL_PAGE_SIZE", 20),
        history_backfill_delay_min_seconds=_non_negative_float(
            values, "HISTORY_BACKFILL_DELAY_MIN_SECONDS", 3.0
        ),
        history_backfill_delay_max_seconds=_non_negative_float(
            values, "HISTORY_BACKFILL_DELAY_MAX_SECONDS", 6.0
        ),
        max_concurrent_history_backfills=_positive_int(
            values, "MAX_CONCURRENT_HISTORY_BACKFILLS", 1
        ),
        notify_on_initial_sync=_boolean(values, "NOTIFY_ON_INITIAL_SYNC", False),
        auto_accept_confidence=_confidence(values, "AUTO_ACCEPT_CONFIDENCE", 0.8),
        llm_enabled=_boolean(values, "LLM_ENABLED", False),
        llm_api_key=_value(values, "LLM_API_KEY", ""),
        llm_base_url=_value(values, "LLM_BASE_URL", ""),
        llm_model=_value(values, "LLM_MODEL", ""),
        llm_timeout_seconds=_positive_float(values, "LLM_TIMEOUT_SECONDS", 20.0),
        llm_auto_accept_confidence=_confidence(
            values, "LLM_AUTO_ACCEPT_CONFIDENCE", 0.90
        ),
        max_backoff_minutes=_positive_int(values, "MAX_BACKOFF_MINUTES", 60),
        scheduler_poll_seconds=_positive_float(values, "SCHEDULER_POLL_SECONDS", 15.0),
        adaptive_scheduler_enabled=_boolean(values, "ADAPTIVE_SCHEDULER_ENABLED", False),
        adaptive_min_interval_minutes=_positive_int(
            values, "ADAPTIVE_MIN_INTERVAL_MINUTES", 5
        ),
        adaptive_max_interval_minutes=_positive_int(
            values, "ADAPTIVE_MAX_INTERVAL_MINUTES", 240
        ),
        telegram_bot_token=_value(values, "TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_value(values, "TELEGRAM_CHAT_ID", ""),
        feishu_webhook_url=_value(values, "FEISHU_WEBHOOK_URL", ""),
        notification_poll_seconds=_positive_float(values, "NOTIFICATION_POLL_SECONDS", 5.0),
        notification_max_attempts=_positive_int(values, "NOTIFICATION_MAX_ATTEMPTS", 8),
        notification_max_backoff_seconds=_positive_int(values, "NOTIFICATION_MAX_BACKOFF_SECONDS", 3600),
        notification_claim_timeout_seconds=_positive_int(values, "NOTIFICATION_CLAIM_TIMEOUT_SECONDS", 300),
        admin_api_token=_value(values, "ADMIN_API_TOKEN", ""),
        app_auth_enabled=_boolean(values, "APP_AUTH_ENABLED", False),
        app_auth_password=_value(values, "APP_AUTH_PASSWORD", ""),
        app_session_secret=_value(values, "APP_SESSION_SECRET", ""),
        app_session_ttl_hours=_positive_int(values, "APP_SESSION_TTL_HOURS", 168),
        app_cookie_secure=_choice(values, "APP_COOKIE_SECURE", "auto", {"auto", "true", "false"}),
        crawler_circuit_breaker_enabled=_boolean(
            values, "CRAWLER_CIRCUIT_BREAKER_ENABLED", True
        ),
        crawler_circuit_failure_threshold=_positive_int(
            values, "CRAWLER_CIRCUIT_FAILURE_THRESHOLD", 3
        ),
        crawler_circuit_open_minutes=_positive_int(
            values, "CRAWLER_CIRCUIT_OPEN_MINUTES", 20
        ),
        douyin_max_concurrent_requests=_positive_int(
            values, "DOUYIN_MAX_CONCURRENT_REQUESTS", 3
        ),
        douyin_min_request_interval_seconds=_non_negative_float(
            values, "DOUYIN_MIN_REQUEST_INTERVAL_SECONDS", 0.5
        ),
        legacy_monitor_enabled=_boolean(values, "LEGACY_MONITOR_ENABLED", False),
        scan_run_retention_days=_positive_int(values, "SCAN_RUN_RETENTION_DAYS", 30),
        backup_retention_count=_positive_int(values, "BACKUP_RETENTION_COUNT", 14),
        auto_maintenance_enabled=_boolean(values, "AUTO_MAINTENANCE_ENABLED", True),
        auto_backup_interval_hours=_positive_int(values, "AUTO_BACKUP_INTERVAL_HOURS", 24),
        maintenance_poll_seconds=_positive_float(values, "MAINTENANCE_POLL_SECONDS", 300),
        wal_checkpoint_interval_hours=_positive_int(values, "WAL_CHECKPOINT_INTERVAL_HOURS", 6),
        raw_json_prune_batch_size=_positive_int(values, "RAW_JSON_PRUNE_BATCH_SIZE", 500),
        ocr_enabled=_boolean(values, "OCR_ENABLED", False),
        ocr_timeout_seconds=_positive_float(values, "OCR_TIMEOUT_SECONDS", 15),
        ocr_api_url=_value(values, "OCR_API_URL", ""),
        ocr_api_key=_value(values, "OCR_API_KEY", ""),
    )
    if settings.llm_enabled:
        missing = [
            name
            for name, value in (
                ("LLM_API_KEY", settings.llm_api_key),
                ("LLM_BASE_URL", settings.llm_base_url),
                ("LLM_MODEL", settings.llm_model),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"启用 LLM 时必须设置: {', '.join(missing)}")
    if settings.ocr_enabled and not settings.ocr_api_url:
        raise ValueError("启用 OCR 时必须设置 OCR_API_URL")
    if settings.app_auth_enabled:
        if not settings.app_auth_password:
            raise ValueError("启用 APP_AUTH_ENABLED 时必须设置 APP_AUTH_PASSWORD")
        if len(settings.app_session_secret.encode("utf-8")) < 32:
            raise ValueError("启用 APP_AUTH_ENABLED 时 APP_SESSION_SECRET 至少需要 32 字节")
    if settings.history_backfill_delay_max_seconds < settings.history_backfill_delay_min_seconds:
        raise ValueError(
            "HISTORY_BACKFILL_DELAY_MAX_SECONDS 不能小于 HISTORY_BACKFILL_DELAY_MIN_SECONDS"
        )
    if settings.adaptive_max_interval_minutes < settings.adaptive_min_interval_minutes:
        raise ValueError(
            "ADAPTIVE_MAX_INTERVAL_MINUTES 不能小于 ADAPTIVE_MIN_INTERVAL_MINUTES"
        )
    return settings


def load_cookie_header(cookie_file: Path) -> str | None:
    """Read common browser-export cookie formats without ever logging secrets."""
    if not cookie_file.is_file():
        return None
    try:
        text = cookie_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return _cookie_header_from_json(parsed)


def _cookie_header_from_json(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("cookie", "Cookie", "cookies"):
            if key in value:
                return _cookie_header_from_json(value[key])
        return None
    if not isinstance(value, list):
        return None
    pairs: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        cookie_value = str(item.get("value") or "").strip()
        if name and cookie_value:
            pairs.append(f"{name}={cookie_value}")
    return "; ".join(pairs) or None


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip("\"'")
    return values


def _value(values: Mapping[str, str], key: str, default: str) -> str:
    return str(values.get(key, default) or "").strip()


def _choice(values: Mapping[str, str], key: str, default: str, choices: set[str]) -> str:
    result = _value(values, key, default).lower()
    if result not in choices:
        raise ValueError(f"{key} 必须是: {', '.join(sorted(choices))}")
    return result


def _path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _database_path(root: Path, database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("目前只支持 sqlite:/// 格式的 DATABASE_URL")
    raw_path = database_url[len(prefix) :]
    if not raw_path:
        raise ValueError("DATABASE_URL 缺少数据库路径")
    return _path(root, raw_path)


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = _value(values, key, str(default))
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是正整数") from exc
    if result <= 0:
        raise ValueError(f"{key} 必须大于 0")
    return result


def _positive_float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = _value(values, key, str(default))
    try:
        result = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是正数") from exc
    if result <= 0:
        raise ValueError(f"{key} 必须大于 0")
    return result


def _non_negative_float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = _value(values, key, str(default))
    try:
        result = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是非负数") from exc
    if result < 0:
        raise ValueError(f"{key} 不能小于 0")
    return result


def _boolean(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = _value(values, key, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{key} 必须是布尔值")


def _confidence(values: Mapping[str, str], key: str, default: float) -> float:
    raw = _value(values, key, str(default))
    try:
        result = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必须是数字") from exc
    if not 0 <= result <= 1:
        raise ValueError(f"{key} 必须在 0 到 1 之间")
    return result
