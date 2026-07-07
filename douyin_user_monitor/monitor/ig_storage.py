from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

CHINA_TZ = timezone(timedelta(hours=8))

DEFAULT_INTERVAL_HOURS = 6.0
DEFAULT_MODE = "interval"

MONITORING_DEFAULTS: Dict[str, Any] = {
    "is_running": False,
    "mode": DEFAULT_MODE,
    "interval_hours": DEFAULT_INTERVAL_HOURS,
    "coverage_hours": 24.0,
    "last_run_at": None,
    "last_run_result": {},
}


def utc_now() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def build_default_state() -> Dict[str, Any]:
    return {"users": [], "monitoring": dict(MONITORING_DEFAULTS)}


class IgStorage:
    """Instagram 监控状态 JSON 持久化。"""

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
        self._normalize(state)
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        content = json.dumps(state, ensure_ascii=False, indent=2)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(self.state_file)

    def _normalize(self, state: Dict[str, Any]) -> None:
        if "users" not in state or not isinstance(state["users"], list):
            state["users"] = []
        if "monitoring" not in state or not isinstance(state["monitoring"], dict):
            state["monitoring"] = dict(MONITORING_DEFAULTS)
        monitoring = state["monitoring"]
        for key, value in MONITORING_DEFAULTS.items():
            monitoring.setdefault(key, value)

    def find_user_by_id(self, user_id: str) -> Dict[str, Any] | None:
        state = self.load_state()
        for user in state["users"]:
            if user.get("id") == user_id:
                return user
        return None

    def find_user_by_username(self, username: str) -> Dict[str, Any] | None:
        state = self.load_state()
        for user in state["users"]:
            if user.get("username") == username:
                return user
        return None

    def add_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        state = self.load_state()
        state["users"].append(user)
        self.save_state(state)
        return user

    def update_user(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any] | None:
        state = self.load_state()
        for user in state["users"]:
            if user.get("id") == user_id:
                user.update(updates)
                self.save_state(state)
                return user
        return None

    def remove_user(self, user_id: str) -> bool:
        state = self.load_state()
        before = len(state["users"])
        state["users"] = [u for u in state["users"] if u.get("id") != user_id]
        if len(state["users"]) < before:
            self.save_state(state)
            return True
        return False

    def update_monitoring(self, updates: Dict[str, Any]) -> None:
        state = self.load_state()
        state["monitoring"].update(updates)
        self.save_state(state)
