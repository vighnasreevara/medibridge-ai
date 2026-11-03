"""Staff directory (names and roles only) and the operational overview."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (ApprovalRecord, CareTeamAssignment, Encounter, HandoffReport, Role, User, WorkflowEvent,
                      WorkflowRun)
from ..security.deps import Principal, require

router = APIRouter(prefix="/api", tags=["staff"])


@router.get("/staff")
def staff(role: Literal["doctor", "nurse", "admin"] | None = None,
          principal: Principal = Depends(require("registry.search")), db: Session = Depends(get_db)) -> dict:
    stmt = select(User).join(Role).where(User.status == "active")
    if role:
        stmt = stmt.where(Role.key == role)
    users = db.scalars(stmt.order_by(User.display_name)).all()
    return {"items": [{"id": str(u.id), "display_name": u.display_name, "role": u.role.key,
                       "specialty": u.doctor_profile.specialty if u.doctor_profile else None} for u in users]}


@router.get("/overview")
def overview(principal: Principal = Depends(require("agents.monitor")), db: Session = Depends(get_db)) -> dict:
    """Counts only (no patient content). Clinicians see their care-team scope; administrators see totals."""
    start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    scoped = principal.role != "admin"
    care = select(CareTeamAssignment.patient_id).where(CareTeamAssignment.user_id == principal.user_id,
                                                       CareTeamAssignment.revoked_at.is_(None))

    def enc_count(*conds) -> int:
        stmt = select(func.count()).select_from(Encounter).where(*conds)
        if scoped:
            stmt = stmt.where(Encounter.patient_id.in_(care))
        return db.scalar(stmt) or 0

    def handoff_count(*statuses) -> int:
        stmt = select(func.count()).select_from(HandoffReport).where(HandoffReport.status.in_(statuses))
        if scoped:
            stmt = stmt.where(HandoffReport.patient_id.in_(care))
        return db.scalar(stmt) or 0

    run_stmt = select(WorkflowRun.status, func.count()).group_by(WorkflowRun.status)
    if scoped:
        run_stmt = run_stmt.where(WorkflowRun.patient_id.in_(care))
    recent = select(WorkflowEvent).order_by(WorkflowEvent.seq.desc()).limit(15)
    if scoped:
        recent = recent.where(WorkflowEvent.patient_id.in_(care))
    approvals_today = select(func.count()).select_from(ApprovalRecord).where(ApprovalRecord.decided_at >= start)
    if scoped:
        approvals_today = approvals_today.where(ApprovalRecord.patient_id.in_(care))
    return {
        "scope": "care_team" if scoped else "all",
        "encounters_today": enc_count(Encounter.started_at >= start, Encounter.started_at < start + timedelta(days=1)),
        "awaiting_intake": enc_count(Encounter.status == "planned", Encounter.started_at >= start),
        "in_progress": enc_count(Encounter.status == "in_progress"),
        "awaiting_review": enc_count(Encounter.status == "awaiting_review"),
        "draft_handoffs": handoff_count("draft", "changes_requested"),
        "pending_approvals": handoff_count("in_review"),
        "approvals_today": db.scalar(approvals_today) or 0,
        "runs_by_status": dict(db.execute(run_stmt).all()),
        "recent_events": [{"seq": ev.seq, "type": ev.type, "node": ev.node, "status": ev.status,
                           "run_id": str(ev.run_id) if ev.run_id else None,
                           "encounter_id": str(ev.encounter_id) if ev.encounter_id else None,
                           "created_at": ev.created_at} for ev in db.scalars(recent)],
    }
