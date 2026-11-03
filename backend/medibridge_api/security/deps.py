"""FastAPI dependencies: authenticated principal, permission checks and patient scope (guardrail 2)."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CareTeamAssignment
from ..services import audit
from . import rbac, sessions


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: str
    display_name: str
    session_id: str
    request_id: str
    ip: str

    def can(self, permission: str) -> bool:
        return rbac.role_has(self.role, permission)

    def audit_ctx(self) -> dict:
        return {"actor_user_id": self.user_id, "actor_role": self.role, "request_id": self.request_id,
                "ip": self.ip}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    resolved = sessions.resolve(db, request.cookies.get(sessions.COOKIE_NAME))
    if resolved.user is None:
        if resolved.reason in {"absolute_timeout", "idle_timeout", "epoch_changed", "password_changed",
                               "user_inactive"}:
            audit.record_now(action="auth.session_ended", category="authentication", outcome="failure",
                             actor_user_id=resolved.session.user_id if resolved.session else None,
                             request_id=_request_id(request), ip=sessions.client_ip(request),
                             details={"reason": resolved.reason})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "auth_required",
                                                                  "reason": resolved.reason})
    user = resolved.user
    return Principal(user_id=user.id, role=user.role.key, display_name=user.display_name,
                     session_id=resolved.session.id, request_id=_request_id(request),
                     ip=sessions.client_ip(request))


def require(permission: str) -> Callable[..., Principal]:
    rbac.role_has("doctor", permission)  # fail at import time on an unknown permission key

    def dependency(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            audit.record_now(action="access.denied", category="access", outcome="denied",
                             resource_type="permission", resource_id=permission,
                             details={"path": request.url.path, "method": request.method},
                             **principal.audit_ctx())
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden", "permission": permission})
        return principal

    return dependency


def has_patient_scope(db: Session, principal: Principal, patient_id: uuid.UUID) -> bool:
    return db.scalar(
        select(CareTeamAssignment.id).where(CareTeamAssignment.user_id == principal.user_id,
                                            CareTeamAssignment.patient_id == patient_id,
                                            CareTeamAssignment.revoked_at.is_(None)).limit(1)
    ) is not None


def ensure_patient_scope(db: Session, principal: Principal, patient_id: uuid.UUID, permission: str) -> None:
    """Raise 404 (not 403, to avoid confirming the record exists) and audit the denial."""
    if not principal.can(permission):
        audit.record_now(action="access.denied", category="access", outcome="denied", patient_id=patient_id,
                         resource_type="permission", resource_id=permission, **principal.audit_ctx())
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden", "permission": permission})
    if rbac.is_patient_scoped(permission) and not has_patient_scope(db, principal, patient_id):
        audit.record_now(action="access.denied", category="access", outcome="denied", patient_id=patient_id,
                         resource_type="patient", resource_id=patient_id,
                         details={"reason": "no_care_team_assignment", "permission": permission},
                         **principal.audit_ctx())
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "not_found"})
