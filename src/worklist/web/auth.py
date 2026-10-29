"""Passwords, server-side sessions and sign-in rate limiting (FR-027a)."""

import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from itsdangerous import BadSignature, Signer
from sqlalchemy import Connection, text

from worklist.config import get_settings

COOKIE_NAME = "wl_session"
_hasher = PasswordHasher()
# Verifying against a dummy hash keeps timing the same for unknown emails.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


def _signer() -> Signer:
    return Signer(get_settings().session_secret, salt="session")


def sign(session_id: str) -> str:
    return _signer().sign(session_id).decode()


def unsign(cookie: str) -> str | None:
    try:
        return _signer().unsign(cookie).decode()
    except BadSignature:
        return None


def authenticate(conn: Connection, email: str, password: str) -> dict | None:
    """Returns the matching user (tenant chosen by password match) or None. Constant-ish time."""
    candidates = conn.execute(text("SELECT * FROM auth_lookup(:e)"), {"e": email.strip()}).mappings().all()
    if not candidates:
        verify_password(_DUMMY_HASH, password)
        return None
    for user in candidates:
        if verify_password(user["password_hash"], password):
            return dict(user)
    return None


def create_session(conn: Connection, user: dict) -> tuple[str, str]:
    session_id = secrets.token_urlsafe(32)  # 256 bits
    csrf = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=get_settings().session_hours)
    conn.execute(
        text("INSERT INTO session (id, user_id, tenant_id, role, csrf_token, expires_at) "
             "VALUES (:id, :u, :t, :r, :c, :e)"),
        {"id": session_id, "u": user["user_id"], "t": user["tenant_id"], "r": user["role"], "c": csrf,
         "e": expires},
    )
    return session_id, csrf


def load_session(conn: Connection, session_id: str) -> dict | None:
    row = conn.execute(text("SELECT * FROM session WHERE id = :id"), {"id": session_id}).mappings().first()
    if row is None:
        return None
    if row["expires_at"] <= datetime.now(UTC):
        conn.execute(text("DELETE FROM session WHERE id = :id"), {"id": session_id})
        return None
    return dict(row)


def delete_session(conn: Connection, session_id: str) -> None:
    conn.execute(text("DELETE FROM session WHERE id = :id"), {"id": session_id})


class RateLimiter:
    """Sliding-window limiter, in memory (one web instance; see README '10x')."""

    def __init__(self, limit: int = 10, window_s: float = 60.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, *keys: str) -> bool:
        """Records an attempt for every key; returns False if any key is over the limit."""
        now = time.monotonic()
        allowed = True
        with self._lock:
            for key in keys:
                hits = self._hits[key]
                while hits and now - hits[0] > self.window_s:
                    hits.popleft()
                if len(hits) >= self.limit:
                    allowed = False
                hits.append(now)
        return allowed

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter()
