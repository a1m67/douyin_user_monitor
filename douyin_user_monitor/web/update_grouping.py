"""Presentation-only grouping for persisted update events."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def group_update_events(
    events: list[dict[str, Any]], *, window_hours: int = 24
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for event in events:
        occurred = str(event.get("occurred_at") or "")
        try:
            timestamp = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        except ValueError:
            timestamp = None
        key = (int(event["show_id"]), int(event.get("season_number") or 1))
        target = groups[-1] if groups and groups[-1]["key"] == key else None
        if target is not None and timestamp is not None and target.get("_timestamp") is not None:
            if abs((target["_timestamp"] - timestamp).total_seconds()) > window_hours * 3600:
                target = None
        if target is None:
            target = {
                "key": key,
                "show_id": event["show_id"],
                "show_title": event["show_title"],
                "season_number": event.get("season_number", 1),
                "episode_numbers": [],
                "events": [],
                "_timestamp": timestamp,
            }
            groups.append(target)
        target["episode_numbers"].append(int(event["episode_number"]))
        target["events"].append(event)
    for group in groups:
        group.pop("key", None)
        group.pop("_timestamp", None)
        numbers = group["episode_numbers"]
        group["episode_start"] = min(numbers)
        group["episode_end"] = max(numbers)
        group["count"] = len(numbers)
    return groups
