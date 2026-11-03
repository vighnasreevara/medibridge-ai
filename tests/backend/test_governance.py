"""Audit, administration and observability API contracts and access rules."""
from __future__ import annotations

from sqlalchemy import select

from medibridge_api.models import AuthSession, User
from tests.conftest import login


def test_audit_endpoints_for_admin_only(client, user_factory):
    admin, doctor = user_factory("admin"), user_factory("doctor")
    login(client, doctor)
    for path in ("/api/audit", "/api/audit/verify", "/api/audit/summary"):
        assert client.get(path).status_code == 403
    login(client, admin)
    log = client.get("/api/audit", params={"limit": 20})
    assert log.status_code == 200 and log.json()["items"]
    assert client.get("/api/audit/verify").json()["intact"] is True
    summary = client.get("/api/audit/summary").json()
    assert {"by_category", "top_actions", "daily"} <= set(summary)


def test_admin_role_change_ends_sessions_immediately(client, db, user_factory):
    from fastapi.testclient import TestClient

    admin, nurse = user_factory("admin"), user_factory("nurse")
    nurse_client = TestClient(client.app, base_url="http://testserver", headers={"X-MediBridge-Request": "1"})
    login(nurse_client, nurse)
    login(client, admin)
    resp = client.patch(f"/api/admin/users/{nurse.id}", json={"role": "doctor"})
    assert resp.status_code == 200 and resp.json()["role"] == "doctor"
    assert nurse_client.get("/api/auth/me").status_code == 401
    db.expire_all()
    assert not db.scalars(select(AuthSession).where(AuthSession.user_id == nurse.id, AuthSession.revoked_at.is_(None))).all()


def test_admin_cannot_change_own_role(client, user_factory):
    admin = user_factory("admin")
    login(client, admin)
    assert client.patch(f"/api/admin/users/{admin.id}", json={"role": "doctor"}).status_code == 409


def test_created_user_gets_a_one_time_password_that_works(client, db, user_factory):
    admin = user_factory("admin")
    login(client, admin)
    resp = client.post("/api/admin/users", json={"email": "new.nurse@medibridge.test", "display_name": "New Nurse",
                                                 "role": "nurse"})
    assert resp.status_code == 201
    temp = resp.json()["temporary_password"]
    assert db.scalar(select(User).where(User.email == "new.nurse@medibridge.test")).password_hash.startswith("$argon2id$")
    assert client.post("/api/auth/login", json={"email": "new.nurse@medibridge.test", "password": temp}).status_code == 200


def test_observability_summary_contains_no_clinical_text(client, user_factory):
    admin = user_factory("admin")
    login(client, admin)
    resp = client.get("/api/observability/summary")
    assert resp.status_code == 200
    body = resp.text.lower()
    for forbidden in ("transcript", "source_phrase", "allerg", "penicillin"):
        assert forbidden not in body
