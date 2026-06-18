import json
from pathlib import Path
from typing import Any, Dict

from douyin_user_monitor.monitor.history_sync import normalize_history_sync_state
from douyin_user_monitor.monitor.profile_parser import build_account_status_fields

DEFAULT_INTERVAL_HOURS = 0.05
DEFAULT_MODE = "interval"
DEFAULT_COVERAGE_HOURS = 24.0
DEFAULT_RANDOM_MIN_SECONDS = 0.0
DEFAULT_RANDOM_MAX_SECONDS = 0.0


MONITORING_DEFAULTS = {
    "is_running": False,
    "mode": DEFAULT_MODE,
    "interval_hours": DEFAULT_INTERVAL_HOURS,
    "coverage_hours": DEFAULT_COVERAGE_HOURS,
    "random_min_seconds": DEFAULT_RANDOM_MIN_SECONDS,
    "random_max_seconds": DEFAULT_RANDOM_MAX_SECONDS,
    "last_run_at": None,
    "last_run_result": {},
}


def utc_now() -> str:
    from datetime import datetime, timedelta, timezone

    china_tz = timezone(timedelta(hours=8))
    return datetime.now(china_tz).isoformat(timespec="seconds")


def build_default_state() -> Dict[str, Any]:
    monitoring = dict(MONITORING_DEFAULTS)
    monitoring["updated_at"] = utc_now()
    return {"users": [], "monitoring": monitoring}


class MonitorStorage:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            return
        self.save_state(build_default_state())

    def load_state(self) -> Dict[str, Any]:
        try:
            raw = self.state_file.read_text(encoding="utf-8")
            state = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            state = build_default_state()
            self.save_state(state)
        self._normalize_state(state)
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        temp_file = self.state_file.with_suffix(".tmp")
        content = json.dumps(state, ensure_ascii=False, indent=2)
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(self.state_file)

    def _normalize_state(self, state: Dict[str, Any]) -> None:
        if "users" not in state or not isinstance(state["users"], list):
            state["users"] = []
        if "monitoring" not in state or not isinstance(state["monitoring"], dict):
            state["monitoring"] = build_default_state()["monitoring"]

        monitoring = state["monitoring"]
        defaults = build_default_state()["monitoring"]
        for key, value in defaults.items():
            monitoring.setdefault(key, value)

        if "interval_hours" not in monitoring:
            legacy_seconds = monitoring.get("interval_seconds")
            if isinstance(legacy_seconds, (int, float)):
                monitoring["interval_hours"] = float(legacy_seconds) / 3600.0
            else:
                monitoring["interval_hours"] = DEFAULT_INTERVAL_HOURS

        for user in state["users"]:
            if not isinstance(user, dict):
                continue
            avatar_url = str(user.get("avatar_url") or "").strip()
            user["avatar_url"] = avatar_url or None
            account_status = build_account_status_fields(
                str(user.get("account_status") or ""),
                user.get("account_status_reason"),
            )
            user.update(account_status)
            account_status_updated_at = str(user.get("account_status_updated_at") or "").strip()
            user["account_status_updated_at"] = account_status_updated_at or None
            if not isinstance(user.get("downloaded_aweme_ids"), list):
                user["downloaded_aweme_ids"] = []
            if not isinstance(user.get("download_records"), list):
                user["download_records"] = []
            normalize_history_sync_state(user)
