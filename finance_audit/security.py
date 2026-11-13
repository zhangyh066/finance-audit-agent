import hashlib
import hmac
import re
import secrets
import threading
import time

from .config import ROLES, SESSION_COOKIE
from .db import audit, new_id, transaction

SESSION_SECONDS = 8 * 60 * 60
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 60
_attempts = {}
_attempts_lock = threading.Lock()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
    return f"pbkdf2_sha256$120000${salt}${digest.hex()}"


DUMMY_HASH = hash_password("not-a-user-password", "00" * 16)


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password):
    if len(password) < 12 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("密码至少 12 位，并同时包含字母和数字")


def validate_username(username):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{2,31}", username):
        raise ValueError("用户名需为 3～32 位字母、数字或 . _ -，且以字母开头")


def public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


class RateLimitError(Exception):
    pass


def _check_rate_limit(key):
    now = time.time()
    with _attempts_lock:
        recent = [timestamp for timestamp in _attempts.get(key, []) if now - timestamp < FAILURE_WINDOW_SECONDS]
        _attempts[key] = recent
        if len(recent) >= MAX_FAILURES:
            raise RateLimitError("登录尝试次数过多，请 60 秒后再试")


def _record_failure(key):
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def login(conn, username, password, client_key="local"):
    username = username.strip()
    key = f"{client_key}:{username.lower()}"
    _check_rate_limit(key)
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
    ).fetchone()
    valid = verify_password(password, row["password_hash"] if row else DUMMY_HASH)
    if row is None or not valid:
        _record_failure(key)
        raise PermissionError("用户名或密码不正确")
    with _attempts_lock:
        _attempts.pop(key, None)
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, row["id"], int(time.time()) + SESSION_SECONDS),
    )
    return public_user(row), token


def current_user(conn, token):
    if not token:
        return None
    row = conn.execute("""
        SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ? AND sessions.expires_at > ? AND users.active = 1
    """, (token, int(time.time()))).fetchone()
    return public_user(row) if row else None


def logout(conn, token):
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def create_user(conn, username, display_name, role, password, actor_id):
    validate_username(username)
    validate_password(password)
    if role not in ROLES:
        raise ValueError("角色不合法")
    display_name = display_name.strip()
    if not 2 <= len(display_name) <= 60:
        raise ValueError("姓名或显示名称应为 2～60 字")
    user_id = new_id()
    with transaction(conn):
        conn.execute("INSERT INTO users(id, username, display_name, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                     (user_id, username, display_name, role, hash_password(password)))
        audit(conn, actor_id, "user.created", detail={"user_id": user_id, "username": username, "role": role})
    return user_id


def list_users(conn):
    return [{**public_user(row), "active": bool(row["active"])} for row in conn.execute(
        "SELECT * FROM users ORDER BY username"
    )]


def set_user_active(conn, user_id, active, actor_id):
    if user_id == actor_id and not active:
        raise ValueError("不能停用当前登录的管理员账号")
    with transaction(conn):
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise LookupError("用户不存在")
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit(conn, actor_id, "user.activated" if active else "user.deactivated",
              detail={"user_id": user_id, "username": row["username"]})


def session_options():
    return {"key": SESSION_COOKIE, "httponly": True, "samesite": "strict", "path": "/", "max_age": SESSION_SECONDS}
