"""Database-backed rate limiting (works across processes; survives restarts)."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from sqlalchemy import delete, func, select

from ..db import get_sessionmaker
from ..models import AuthAttempt
from ..models.base import utcnow

# (max events, window)
LOGIN_PER_IP = (30, timedelta(minutes=15))
LOGIN_FAILURES_PER_ACCOUNT = (8, timedelta(minutes=15))


def account_key(email: str) -> str:
    # The ledger stores a hash, not the email address.
    return "login:user:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()


def ip_key(ip: str) -> str:
    return f"login:ip:{ip}"


def count(key: str, window: timedelta) -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(AuthAttempt)
                         .where(AuthAttempt.key == key, AuthAttempt.created_at > utcnow() - window)) or 0


def exceeded(key: str, limit: tuple[int, timedelta]) -> bool:
    return count(key, limit[1]) >= limit[0]


def hit(key: str) -> None:
    with get_sessionmaker()() as db:
        db.add(AuthAttempt(key=key))
        db.commit()


def clear(key: str) -> None:
    with get_sessionmaker()() as db:
        db.execute(delete(AuthAttempt).where(AuthAttempt.key == key))
        db.commit()


def prune(older_than: timedelta = timedelta(days=1)) -> int:
    with get_sessionmaker()() as db:
        result = db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < utcnow() - older_than))
        db.commit()
        return result.rowcount or 0
