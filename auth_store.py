"""
auth_store.py - lightweight file-backed user auth storage.
"""
from __future__ import annotations

import json
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

USERS_FILE = Path(__file__).with_name("users.json")


def _load_users() -> list[dict]:
    if not USERS_FILE.exists():
        return []
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_users(users: list[dict]) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def find_user_by_email(email: str) -> dict | None:
    target = (email or "").strip().lower()
    if not target:
        return None
    return next((user for user in _load_users() if user.get("email", "").lower() == target), None)


def create_user(email: str, password: str, name: str = "") -> tuple[bool, str]:
    normalized_email = (email or "").strip().lower()
    display_name = (name or "").strip()
    if not normalized_email or "@" not in normalized_email:
        return False, "Enter a valid email address."
    if len(password or "") < 6:
        return False, "Password must be at least 6 characters."
    users = _load_users()
    if any(user.get("email", "").lower() == normalized_email for user in users):
        return False, "An account with this email already exists."
    users.append(
        {
            "email": normalized_email,
            "name": display_name,
            "password_hash": generate_password_hash(password),
        }
    )
    _save_users(users)
    return True, "Account created."


def authenticate_user(email: str, password: str) -> dict | None:
    user = find_user_by_email(email)
    if not user:
        return None
    if not check_password_hash(user.get("password_hash", ""), password or ""):
        return None
    return user
