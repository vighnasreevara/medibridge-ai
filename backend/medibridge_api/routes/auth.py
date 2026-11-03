"""Authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..models.base import utcnow
from ..security import passwords, ratelimit, rbac, sessions
from ..security.deps import Principal, current_principal
from ..services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_FAILURE = {"code": "invalid_credentials", "message": "Email or password is incorrect."}


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=passwords.MAX_LENGTH)


class MeOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    role_label: str
    permissions: list[str]
    session_idle_minutes: int


def _me(user: User) -> MeOut:
    from ..config import get_settings

    return MeOut(id=str(user.id), email=user.email, display_name=user.display_name, role=user.role.key,
                 role_label=user.role.label, permissions=sorted(rbac.ROLES[user.role.key]["permissions"]),
                 session_idle_minutes=get_settings().medibridge_session_idle_minutes)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    ip = sessions.client_ip(request)
    email = body.email.strip().lower()
    account = ratelimit.account_key(email)
    ctx = {"request_id": request.state.request_id, "ip": ip}

    if ratelimit.exceeded(ratelimit.ip_key(ip), ratelimit.LOGIN_PER_IP) or \
            ratelimit.exceeded(account, ratelimit.LOGIN_FAILURES_PER_ACCOUNT):
        audit.record_now(action="auth.login", category="security", outcome="denied",
                         details={"reason": "rate_limited"}, **ctx)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail={"code": "rate_limited", "message": "Too many attempts. Try again later."})
    ratelimit.hit(ratelimit.ip_key(ip))

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    ok, new_hash = passwords.verify(user.password_hash if user else None, body.password)
    if not ok or user is None or user.status != "active":
        ratelimit.hit(account)
        audit.record_now(action="auth.login", category="authentication", outcome="failure",
                         actor_user_id=user.id if user else None,
                         details={"reason": "inactive" if ok and user else "bad_credentials"}, **ctx)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=GENERIC_FAILURE)

    if new_hash:
        user.password_hash = new_hash
    user.last_login_at = utcnow()
    token = sessions.create(db, user, request)
    audit.record(db, action="auth.login", category="authentication", outcome="success", actor_user_id=user.id,
                 actor_role=user.role.key, **ctx)
    db.commit()
    ratelimit.clear(account)
    sessions.set_cookie(response, token, request)
    return _me(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           principal: Principal = Depends(current_principal)) -> Response:
    session = db.get(sessions.AuthSession, principal.session_id)
    if session:
        sessions.revoke(db, session, "logout")
    audit.record(db, action="auth.logout", category="authentication", outcome="success", **principal.audit_ctx())
    db.commit()
    sessions.clear_cookie(response, request)
    response.status_code = 204
    return response


@router.get("/session")
def session_probe(request: Request, db: Session = Depends(get_db)) -> dict:
    """Signed-in check that answers 200 either way, so the sign-in page does not need a failing request."""
    resolved = sessions.resolve(db, request.cookies.get(sessions.COOKIE_NAME))
    if resolved.user is None:
        expired = resolved.reason in {"absolute_timeout", "idle_timeout", "epoch_changed", "password_changed",
                                      "user_inactive", "revoked"}
        return {"authenticated": False, "reason": resolved.reason if expired else None}
    return {"authenticated": True, "user": _me(resolved.user).model_dump()}


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> MeOut:
    return _me(db.get(User, principal.user_id))


@router.get("/sessions")
def my_sessions(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    return [
        {"id": s.id[:12], "current": s.id == principal.session_id, "created_at": s.created_at,
         "last_seen_at": s.last_seen_at, "expires_at": s.expires_at, "ip": s.ip, "user_agent": s.user_agent}
        for s in sessions.active_for_user(db, principal.user_id)
    ]


@router.post("/sessions/revoke-others", status_code=200)
def revoke_other_sessions(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    count = 0
    for s in sessions.active_for_user(db, principal.user_id):
        if s.id != principal.session_id:
            sessions.revoke(db, s, "user_revoked")
            count += 1
    audit.record(db, action="auth.sessions_revoked", category="authentication", outcome="success",
                 details={"count": count}, **principal.audit_ctx())
    db.commit()
    return {"revoked": count}
