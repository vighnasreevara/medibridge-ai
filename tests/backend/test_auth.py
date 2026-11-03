"""Authentication, sessions, CSRF and rate limiting."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from medibridge_api.models import AuditLog, AuthSession, User
from medibridge_api.models.base import utcnow
from medibridge_api.security import sessions
from tests.conftest import TEST_PASSWORD, login


def _session_row(db, client) -> AuthSession:
    token = client.cookies.get(sessions.COOKIE_NAME)
    db.expire_all()
    return db.get(AuthSession, sessions.digest(token))


def test_password_is_hashed_with_argon2id(user_factory):
    user = user_factory("doctor")
    assert user.password_hash.startswith("$argon2id$")
    assert TEST_PASSWORD not in user.password_hash


def test_login_sets_strict_httponly_cookie_and_returns_role(client, user_factory):
    user = user_factory("doctor")
    resp = client.post("/api/auth/login", json={"email": user.email.upper(), "password": TEST_PASSWORD})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    body = resp.json()
    assert body["role"] == "doctor"
    assert "handoff.approve" in body["permissions"]
    assert client.get("/api/auth/me").json()["email"] == user.email


def test_session_token_is_not_stored_in_plaintext(client, db, user_factory):
    user = user_factory("nurse")
    login(client, user)
    token = client.cookies.get(sessions.COOKIE_NAME)
    assert db.get(AuthSession, token) is None
    assert db.get(AuthSession, sessions.digest(token)) is not None


def test_wrong_password_and_unknown_email_get_identical_responses(client, db, user_factory):
    user = user_factory("doctor")
    bad = client.post("/api/auth/login", json={"email": user.email, "password": "not-the-password-123"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@medibridge.test", "password": "x" * 14})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    failure = db.scalars(select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.outcome == "failure",
                                                AuditLog.actor_user_id == user.id)).first()
    assert failure is not None
    assert user.email not in str(failure.details) and "password" not in str(failure.details).lower()


def test_disabled_user_cannot_sign_in(client, user_factory):
    user = user_factory("doctor", status="disabled")
    resp = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert resp.status_code == 401


def test_state_changing_request_without_csrf_header_is_rejected(client, user_factory):
    user = user_factory("doctor")
    resp = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD},
                       headers={"X-MediBridge-Request": ""})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "csrf"


def test_foreign_origin_is_rejected(client, user_factory):
    user = user_factory("doctor")
    resp = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_unknown_host_is_rejected(client):
    assert client.get("/api/health/live", headers={"Host": "attacker.example"}).status_code == 421


def test_security_headers_present(client):
    resp = client.get("/api/health/live")
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["cache-control"] == "no-store"


def test_logout_revokes_session_server_side(client, user_factory):
    user = user_factory("doctor")
    login(client, user)
    token = client.cookies.get(sessions.COOKIE_NAME)
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set(sessions.COOKIE_NAME, token)  # replay the old cookie
    assert client.get("/api/auth/me").status_code == 401


def test_idle_timeout_expires_session_and_is_audited(client, db, user_factory):
    user = user_factory("nurse")
    login(client, user)
    row = _session_row(db, client)
    db.execute(update(AuthSession).where(AuthSession.id == row.id)
               .values(last_seen_at=utcnow() - timedelta(hours=2)))
    db.commit()
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "idle_timeout"
    assert db.scalars(select(AuditLog).where(AuditLog.action == "auth.session_ended",
                                             AuditLog.actor_user_id == user.id)).first() is not None


def test_absolute_timeout_expires_session(client, db, user_factory):
    user = user_factory("doctor")
    login(client, user)
    row = _session_row(db, client)
    db.execute(update(AuthSession).where(AuthSession.id == row.id).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/auth/me").json()["detail"]["reason"] == "absolute_timeout"


def test_role_change_invalidates_existing_sessions(client, db, user_factory):
    user = user_factory("doctor")
    login(client, user)
    db.execute(update(User).where(User.id == user.id).values(session_epoch=User.session_epoch + 1))
    db.commit()
    assert client.get("/api/auth/me").json()["detail"]["reason"] == "epoch_changed"


def test_password_change_invalidates_older_sessions(client, db, user_factory):
    user = user_factory("doctor")
    login(client, user)
    db.execute(update(User).where(User.id == user.id).values(password_changed_at=utcnow() + timedelta(seconds=1)))
    db.commit()
    assert client.get("/api/auth/me").json()["detail"]["reason"] == "password_changed"


def test_account_lockout_after_repeated_failures(client, user_factory):
    user = user_factory("doctor")
    for _ in range(8):
        assert client.post("/api/auth/login", json={"email": user.email, "password": "wrong-password-xx"}
                           ).status_code == 401
    # Even the correct password is refused while the account is rate limited.
    resp = client.post("/api/auth/login", json={"email": user.email, "password": TEST_PASSWORD})
    assert resp.status_code == 429


def test_revoke_other_sessions(client, user_factory):
    from fastapi.testclient import TestClient

    user = user_factory("doctor")
    login(client, user)
    other = TestClient(client.app, base_url="http://testserver", headers={"X-MediBridge-Request": "1"})
    login(other, user)
    assert client.post("/api/auth/sessions/revoke-others").json()["revoked"] == 1
    assert other.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200
