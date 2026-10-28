"""Passwords, sessions, roles and the audit trail."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response

from . import db
from .config import settings

SESSION_COOKIE = "zr_session"
ROLES = ("admin", "analyst", "viewer")


# --------------------------------------------------------------------------- errors

def fail(status: int, code: str, message: str, **extra) -> HTTPException:
    """Errors carry a stable `code` the UI translates (EN/AR), an English fallback and any details (`extra`)."""
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


# --------------------------------------------------------------------------- passwords (scrypt, stdlib)

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 8:
        return "password_too_short"
    if not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
        return "password_too_simple"
    return None


# --------------------------------------------------------------------------- login throttling

_attempts: dict[str, deque] = defaultdict(deque)


def throttle(key: str, limit: int = 8, window: int = 300) -> None:
    now = time.monotonic()
    q = _attempts[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise fail(429, "too_many_attempts", "Too many sign-in attempts. Wait a few minutes and try again.")
    q.append(now)


def clear_throttle(key: str) -> None:
    _attempts.pop(key, None)


# --------------------------------------------------------------------------- sessions

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def create_session(response: Response, request: Request, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(hours=settings.session_hours)
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) VALUES (:t, :u, :e, :ip, :ua)",
        t=_hash_token(token), u=user_id, e=expires, ip=client_ip(request),
        ua=(request.headers.get("user-agent") or "")[:255],
    )
    db.execute("DELETE FROM sessions WHERE expires_at < NOW()")
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=settings.session_hours * 3600,
                        path="/")


def destroy_session(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = :t", t=_hash_token(token))
    response.delete_cookie(SESSION_COOKIE, path="/")


PUBLIC_USER = "id, name, email, role, is_active, must_change_password, lang, created_at, last_login_at"


def current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise fail(401, "not_signed_in", "Please sign in.")
    user = db.fetch_one(
        f"SELECT {', '.join('u.' + c.strip() for c in PUBLIC_USER.split(','))} FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = :t AND s.expires_at > NOW() AND u.is_active = 1",
        t=_hash_token(token),
    )
    if not user:
        raise fail(401, "session_expired", "Your session has expired. Please sign in again.")
    request.state.user = user
    return user


def require(*roles: str):
    """Dependency: the signed-in user must hold one of `roles`."""
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise fail(403, "forbidden", "You do not have permission to do this.")
        return user
    return dep


can_edit = require("admin", "analyst")
admin_only = require("admin")


# --------------------------------------------------------------------------- audit

def audit(request: Request | None, user: dict | None, action: str, entity: str | None = None,
          entity_id: object = None, details: dict | None = None) -> None:
    db.execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, details, ip) VALUES (:u, :a, :e, :i, :d, :ip)",
        u=user["id"] if user else None, a=action, e=entity, i=None if entity_id is None else str(entity_id),
        d=db.to_json(details) if details else None, ip=client_ip(request) if request else None,
    )


def seed_admin() -> None:
    if db.scalar("SELECT COUNT(*) FROM users") == 0:
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, must_change_password) "
            "VALUES ('Administrator', :e, :h, 'admin', 1)",
            e=settings.admin_email.lower(), h=hash_password(settings.admin_password),
        )
