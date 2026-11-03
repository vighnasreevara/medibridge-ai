"""Workflow event log: the single source for live progress in the UI.

Events carry identifiers, statuses and counts only (no clinical text). ``emit`` joins the caller's
transaction, so an event becomes visible exactly when the state change it reports is committed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CareTeamAssignment, WorkflowEvent

_FORBIDDEN_ATTR_KEYS = {"text", "transcript", "content", "body", "name", "phrase", "query", "email", "phone"}


def emit(db: Session, type: str, *, run_id: uuid.UUID | None = None, encounter_id: uuid.UUID | None = None,
         patient_id: uuid.UUID | None = None, agent_id: str | None = None, node: str | None = None,
         tool_name: str | None = None, status: str | None = None, actor_user_id: uuid.UUID | None = None,
         **attrs: Any) -> WorkflowEvent:
    leaked = _FORBIDDEN_ATTR_KEYS & {k.lower() for k in attrs}
    if leaked:
        raise ValueError(f"Workflow events must not carry content fields: {sorted(leaked)}")
    event = WorkflowEvent(type=type, run_id=run_id, encounter_id=encounter_id, patient_id=patient_id,
                          agent_id=agent_id, node=node, tool_name=tool_name, status=status, attrs=attrs,
                          actor_user_id=actor_user_id)
    db.add(event)
    return event


def serialize(e: WorkflowEvent) -> dict:
    return {"seq": e.seq, "event_id": str(e.event_id), "type": e.type,
            "run_id": str(e.run_id) if e.run_id else None,
            "encounter_id": str(e.encounter_id) if e.encounter_id else None,
            "patient_id": str(e.patient_id) if e.patient_id else None, "agent_id": e.agent_id, "node": e.node,
            "tool_name": e.tool_name, "status": e.status, "attrs": e.attrs,
            "created_at": e.created_at.isoformat()}


def visible_patient_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.scalars(select(CareTeamAssignment.patient_id).where(CareTeamAssignment.user_id == user_id,
                                                                       CareTeamAssignment.revoked_at.is_(None))))


def fetch(db: Session, *, after: int, limit: int, scope: set[uuid.UUID] | None) -> list[WorkflowEvent]:
    """Events with seq > after; ``scope=None`` means unrestricted (administrators: identifiers only)."""
    stmt = select(WorkflowEvent).where(WorkflowEvent.seq > after).order_by(WorkflowEvent.seq).limit(limit)
    if scope is not None:
        stmt = stmt.where(WorkflowEvent.patient_id.in_(scope) | WorkflowEvent.patient_id.is_(None))
    return list(db.scalars(stmt))


def last_seq(db: Session) -> int:
    return db.scalar(select(WorkflowEvent.seq).order_by(WorkflowEvent.seq.desc()).limit(1)) or 0
