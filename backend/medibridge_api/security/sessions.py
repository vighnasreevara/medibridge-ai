"""Server-side sessions. The cookie holds a random token; the database stores only its SHA-256.

A session is valid only while: not revoked, before its absolute expiry, within the idle timeout,
the user is active, the user's session epoch is unchanged (role change / forced sign-out),
and it was issued after the last password change.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuthSession, User
from ..models.base import utcnow

COOKIE_NAME = "mb_session"
TOUCH_INTERVAL = timedelta(seconds=60)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    # Local deployment: no trusted proxy, so X-Forwarded-For is ignored on purpose.
    return request.client.host if request.client else ""


def _secure(request: Request) -> bool:
    mode = get_settings().medibridge_secure_cookies
    return request.url.scheme == "https" if mode == "auto" else mode == "1"


def create(db: Session, user: User, request: Request) -> str:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(AuthSession(
        id=digest(token), user_id=user.id, session_epoch=user.session_epoch, created_at=now,
        expires_at=now + timedelta(minutes=s.medibridge_session_absolute_minutes), last_seen_at=now,
        ip=client_ip(request), user_agent=(request.headers.get("user-agent") or "")[:300],
    ))
    return token


@dataclass
class Resolved:
    session: AuthSession | None
    user: User | None
    reason: str | None  # None when valid; otherwise a reason code for audit


def resolve(db: Session, token: str | None) -> Resolved:
    if not token:
        return Resolved(None, None, "no_session")
    session = db.get(AuthSession, digest(token))
    if session is None:
        return Resolved(None, None, "unknown_session")
    if session.revoked_at is not None:
        return Resolved(session, None, "revoked")
    now = utcnow()
    idle = timedelta(minutes=get_settings().medibridge_session_idle_minutes)
    user = db.get(User, session.user_id)
    reason = None
    if now >= session.expires_at:
        reason = "absolute_timeout"
    elif now - session.last_seen_at >= idle:
        reason = "idle_timeout"
    elif user is None or user.status != "active":
        reason = "user_inactive"
    elif user.session_epoch != session.session_epoch:
        reason = "epoch_changed"
    elif session.created_at < user.password_changed_at:
        reason = "password_changed"
    if reason:
        revoke(db, session, reason)
        db.commit()
        return Resolved(session, None, reason)
    if now - session.last_seen_at >= TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return Resolved(session, user, None)


def revoke(db: Session, session: AuthSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = reason


def revoke_all_for_user(db: Session, user_id, reason: str) -> int:
    result = db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                        .values(revoked_at=utcnow(), revoked_reason=reason))
    return result.rowcount or 0


def active_for_user(db: Session, user_id) -> list[AuthSession]:
    return list(db.scalars(select(AuthSession).where(AuthSession.user_id == user_id,
                                                     AuthSession.revoked_at.is_(None))
                           .order_by(AuthSession.created_at.desc())))


def set_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=_secure(request), samesite="strict", path="/",
        max_age=get_settings().medibridge_session_absolute_minutes * 60,
    )


def clear_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=_secure(request), httponly=True, samesite="strict")
