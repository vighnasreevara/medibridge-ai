"""Shared fixtures. Tests run against a fresh ``medibridge_test`` database on the local Postgres container,
built through the real Alembic migrations, so database-level guardrails are exercised exactly as deployed.
All data is synthetic.
"""
from __future__ import annotations

import os

os.environ["POSTGRES_DB"] = "medibridge_test"
os.environ["MEDIBRIDGE_ENV"] = "test"
os.environ["MEDIBRIDGE_SECURE_COOKIES"] = "0"

import itertools  # noqa: E402
import secrets  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, delete, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from medibridge_api.config import REPO_ROOT, get_settings  # noqa: E402
from medibridge_api.db import get_sessionmaker  # noqa: E402
from medibridge_api.models import AuthAttempt, CareTeamAssignment, Patient, Role, User  # noqa: E402
from medibridge_api.security import passwords  # noqa: E402
from medibridge_seed.reference import sync_rbac  # noqa: E402

TEST_PASSWORD = "Synthetic-Test-Passphrase-42"
_MRN = itertools.count(900001)  # seeded MRNs are below MB102000
_TEST_HASH = passwords.hash_password(TEST_PASSWORD)


def _admin_url(db: str) -> str:
    s = get_settings()
    return (f"postgresql+psycopg://{s.postgres_owner_user}:{s.postgres_owner_password.get_secret_value()}"
            f"@{s.postgres_host}:{s.postgres_port}/{db}")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    s = get_settings()
    assert s.postgres_db == "medibridge_test", "tests must never run against the main database"
    admin = create_engine(_admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS medibridge_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE medibridge_test"))
        conn.execute(text(f'GRANT CONNECT ON DATABASE medibridge_test TO "{s.postgres_app_user}"'))
    admin.dispose()
    owner = create_engine(_admin_url("medibridge_test"))
    with owner.begin() as conn:
        conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
    cfg = Config(str(Path(REPO_ROOT) / "database" / "alembic.ini"))
    command.upgrade(cfg, "head")
    sync_rbac(owner)
    yield owner
    owner.dispose()


@pytest.fixture
def owner_engine(test_database):
    return test_database


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


@pytest.fixture(scope="session", autouse=True)
def _drain_runs():
    """Let in-flight workflow runs reach a pause before the interpreter shuts their executors down."""
    yield
    import time

    from medibridge_agents import runner

    deadline = time.time() + 30
    while runner._active and time.time() < deadline:
        time.sleep(0.2)


@pytest.fixture(scope="session")
def a2a_apps():
    from medibridge_agents.a2a.registry import REMOTE_AGENTS
    from medibridge_agents.a2a.server import create_app

    return {agent_id: create_app(agent_id) for agent_id in REMOTE_AGENTS}


@pytest.fixture(autouse=True)
def a2a_in_process(a2a_apps):
    """Route the supervisor's A2A calls to the real agent apps in this process (full protocol, no ports).

    Tests may replace an entry to simulate an agent that is down or misbehaving.
    """
    import httpx

    from medibridge_agents.a2a import client as a2a_client

    routes: dict = dict(a2a_apps)

    def factory(agent_id: str):
        app = routes.get(agent_id)
        return httpx.ASGITransport(app=app) if app is not None else _Refuse()

    a2a_client.use_transport(factory)
    yield routes
    a2a_client.use_transport(None)


def _Refuse():
    """An httpx transport for an agent that is not running."""
    import httpx

    class Refuse(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused (test: agent down)", request=request)

    return Refuse()


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    with get_sessionmaker()() as session:
        session.execute(delete(AuthAttempt))
        session.commit()


@pytest.fixture
def client():
    from medibridge_api.main import create_app

    with TestClient(create_app(), base_url="http://testserver",
                    headers={"X-MediBridge-Request": "1"}) as c:
        yield c


def make_user(db: Session, role: str, **overrides) -> User:
    role_row = db.scalar(select(Role).where(Role.key == role))
    tag = secrets.token_hex(4)
    user = User(email=overrides.pop("email", f"{role}.{tag}@medibridge.test"),
                display_name=overrides.pop("display_name", f"Test {role.title()} {tag}"),
                password_hash=_TEST_HASH, role_id=role_row.id, **overrides)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def user_factory(db):
    return lambda role, **kw: make_user(db, role, **kw)


def make_patient(db: Session, **overrides) -> Patient:
    from datetime import date

    tag = secrets.token_hex(3)
    patient = Patient(mrn=overrides.pop("mrn", f"MB{next(_MRN)}"), given_name=overrides.pop("given_name", "Testa"),
                      family_name=overrides.pop("family_name", f"Synthetic{tag}"),
                      birth_date=overrides.pop("birth_date", date(1980, 1, 1)),
                      sex_at_birth=overrides.pop("sex_at_birth", "female"), **overrides)
    db.add(patient)
    db.commit()
    return patient


@pytest.fixture
def patient_factory(db):
    return lambda **kw: make_patient(db, **kw)


def assign(db: Session, user: User, patient: Patient, role: str = "attending") -> None:
    db.add(CareTeamAssignment(id=uuid.uuid4(), patient_id=patient.id, user_id=user.id, role=role, reason="test"))
    db.commit()


def login(client: TestClient, user: User) -> dict:
    resp = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()
