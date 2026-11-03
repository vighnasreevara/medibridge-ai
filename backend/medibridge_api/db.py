"""SQLAlchemy engine and session factory. The API connects as the least-privilege app role."""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url("app"),
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        # Never echo SQL: statements carry clinical values.
        echo=False,
        connect_args={"connect_timeout": 5, "application_name": "medibridge-api"},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one transaction-scoped session per request. Commit is explicit in services."""
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
