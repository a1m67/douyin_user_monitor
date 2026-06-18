from typing import Any, Dict, List, Optional


def find_user_by_id(users: List[Dict[str, Any]], user_id: str) -> Optional[Dict[str, Any]]:
    for user in users:
        if user.get("id") == user_id:
            return user
    return None


def find_user_by_sec_uid(users: List[Dict[str, Any]], sec_user_id: str) -> Optional[Dict[str, Any]]:
    for user in users:
        if user.get("sec_user_id") == sec_user_id:
            return user
    return None
