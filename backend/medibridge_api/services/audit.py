"""Audit logging (guardrail 12).

``record`` adds the event to the caller's transaction, so it commits or rolls back with the change it
describes. ``record_now`` commits immediately in its own transaction; use it for events that must
survive the request failing (denied access, failed sign-in, guardrail blocks).

``details`` must never contain clinical content or credentials: identifiers, counts and reason codes only.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import get_sessionmaker
from ..models import AuditLog

_FORBIDDEN_DETAIL_KEYS = {"password", "token", "transcript", "text", "content", "body", "email", "phone", "address"}


def _clean(details: dict[str, Any] | None) -> dict[str, Any]:
    details = dict(details or {})
    leaked = _FORBIDDEN_DETAIL_KEYS & {k.lower() for k in details}
    if leaked:
        raise ValueError(f"Audit details must not carry sensitive fields: {sorted(leaked)}")
    return details


def _build(*, action: str, category: str, outcome: str, actor_user_id: uuid.UUID | None = None,
           actor_role: str | None = None, patient_id: uuid.UUID | None = None, encounter_id: uuid.UUID | None = None,
           resource_type: str | None = None, resource_id: str | uuid.UUID | None = None,
           request_id: str | None = None, ip: str | None = None, details: dict | None = None) -> AuditLog:
    return AuditLog(
        action=action, category=category, outcome=outcome, actor_user_id=actor_user_id, actor_role=actor_role,
        patient_id=patient_id, encounter_id=encounter_id, resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None, request_id=request_id, ip=ip,
        details=_clean(details),
    )


def record(db: Session, **kwargs: Any) -> AuditLog:
    entry = _build(**kwargs)
    db.add(entry)
    return entry


def record_now(**kwargs: Any) -> None:
    entry = _build(**kwargs)
    with get_sessionmaker()() as db:
        db.add(entry)
        db.commit()


def verify_chain(db: Session) -> dict:
    problems = db.execute(text("SELECT seq, problem FROM audit.verify_chain() LIMIT 20")).all()
    count = db.scalar(select(AuditLog.seq).order_by(AuditLog.seq.desc()).limit(1)) or 0
    head = db.scalar(select(AuditLog.row_hash).order_by(AuditLog.seq.desc()).limit(1))
    return {
        "intact": not problems,
        "last_seq": count,
        "head_hash": head,
        "problems": [{"seq": p.seq, "problem": p.problem} for p in problems],
    }
