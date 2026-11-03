"""Encounters: worklist, creation, two-identifier verification (guardrail 1) and consent."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CareTeamAssignment, ConsentRecord, Encounter, HandoffReport, Patient, User, WorkflowRun
from ..models.base import utcnow
from ..security.deps import Principal, ensure_patient_scope, has_patient_scope, require
from ..services import audit, clinical_views as views

router = APIRouter(prefix="/api/encounters", tags=["encounters"])

OPEN_STATUSES = ("planned", "in_progress", "awaiting_review")
CONSENT_STATEMENT_VERSION = "intake-audio-v1"


class EncounterIn(BaseModel):
    patient_id: uuid.UUID
    encounter_class: Literal["ambulatory", "emergency", "inpatient", "virtual"] = "ambulatory"
    chief_complaint: str = Field("", max_length=300)
    attending_user_id: uuid.UUID


class VerifyIdentityIn(BaseModel):
    """The operator re-enters two identifiers read from the patient (wristband / photo ID + stated DOB)."""
    method: Literal["wristband+dob", "photo_id+dob", "two_identifiers_verbal"]
    mrn: str = Field(min_length=3, max_length=20)
    birth_date: date


class ConsentIn(BaseModel):
    consent_type: Literal["audio_recording", "ai_processing"]
    status: Literal["granted", "declined", "withdrawn"]
    method: Literal["verbal_witnessed", "written", "electronic"]
    statement_version: str = Field(CONSENT_STATEMENT_VERSION, max_length=40)


def get_scoped_encounter(db: Session, principal: Principal, encounter_id: uuid.UUID, permission: str) -> Encounter:
    e = db.get(Encounter, encounter_id)
    if e is None:
        raise HTTPException(404, detail={"code": "not_found"})
    ensure_patient_scope(db, principal, e.patient_id, permission)
    return e


def consent_state(db: Session, encounter: Encounter) -> dict:
    """Latest decision per consent type for this encounter."""
    rows = db.scalars(select(ConsentRecord).where(ConsentRecord.encounter_id == encounter.id)
                      .order_by(ConsentRecord.recorded_at)).all()
    state: dict[str, dict] = {}
    for r in rows:
        state[r.consent_type] = {"status": r.status, "method": r.method, "recorded_at": r.recorded_at,
                                 "statement_version": r.statement_version, "id": str(r.id)}
    return state


def _detail(db: Session, e: Encounter) -> dict:
    names = dict(db.execute(select(User.id, User.display_name)).all())
    run = db.scalars(select(WorkflowRun).where(WorkflowRun.encounter_id == e.id)
                     .order_by(WorkflowRun.created_at.desc()).limit(1)).first()
    handoff = db.scalars(select(HandoffReport).where(HandoffReport.encounter_id == e.id)
                         .order_by(HandoffReport.created_at.desc()).limit(1)).first()
    return {**views.encounter_view(e, attending_name=names.get(e.attending_user_id)),
            "patient": views.patient_summary(e.patient, in_care_team=True),
            "consent": consent_state(db, e),
            "latest_run": {"id": str(run.id), "status": run.status, "current_node": run.current_node,
                           "waiting_gate": run.waiting_gate, "error_code": run.error_code} if run else None,
            "handoff": {"id": str(handoff.id), "status": handoff.status, "current_version": handoff.current_version,
                        "approved_version": handoff.approved_version} if handoff else None}


@router.get("")
def worklist(day: date | None = None, status: str | None = Query(None, max_length=40),
             scope: Literal["mine", "all"] = "mine", principal: Principal = Depends(require("encounter.read")),
             db: Session = Depends(get_db)) -> dict:
    """Encounters for care-team patients. ``scope=all`` still only returns patients in the caller's care team."""
    stmt = (select(Encounter).join(CareTeamAssignment, (CareTeamAssignment.patient_id == Encounter.patient_id)
                                   & (CareTeamAssignment.user_id == principal.user_id)
                                   & CareTeamAssignment.revoked_at.is_(None)))
    if day:
        start = datetime.combine(day, time.min, tzinfo=UTC)
        stmt = stmt.where(Encounter.started_at >= start, Encounter.started_at < start + timedelta(days=1))
    if status:
        stmt = stmt.where(Encounter.status.in_(status.split(",")))
    if scope == "mine":
        stmt = stmt.where((Encounter.attending_user_id == principal.user_id) | (Encounter.created_by == principal.user_id)
                          | (Encounter.status.in_(OPEN_STATUSES)))
    rows = db.scalars(stmt.order_by(Encounter.started_at).distinct().limit(200)).all()
    audit.record(db, action="encounter.list", category="access", outcome="allowed", resource_type="worklist",
                 details={"count": len(rows), "scope": scope}, **principal.audit_ctx())
    db.commit()
    return {"items": [_detail(db, e) for e in rows]}


@router.post("", status_code=201)
def create(body: EncounterIn, principal: Principal = Depends(require("encounter.create")),
           db: Session = Depends(get_db)) -> dict:
    patient = db.get(Patient, body.patient_id)
    if patient is None or patient.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    attending = db.get(User, body.attending_user_id)
    if attending is None or attending.status != "active" or attending.role.key != "doctor":
        raise HTTPException(422, detail={"code": "invalid_attending", "message": "Attending must be an active doctor."})
    existing = db.scalars(select(Encounter).where(Encounter.patient_id == patient.id,
                                                  Encounter.status.in_(OPEN_STATUSES),
                                                  Encounter.started_at >= utcnow() - timedelta(hours=24))).first()
    if existing:
        raise HTTPException(409, detail={"code": "open_encounter_exists", "encounter_id": str(existing.id),
                                         "message": "This patient already has an open encounter today."})
    e = Encounter(id=uuid.uuid4(), patient_id=patient.id, encounter_class=body.encounter_class, status="planned",
                  chief_complaint=body.chief_complaint.strip(), attending_user_id=attending.id,
                  created_by=principal.user_id, started_at=utcnow(), location="MediBridge Demo Hospital (fictional)")
    db.add(e)
    db.flush()
    for user_id, role, reason in ((principal.user_id, "intake" if principal.role == "nurse" else "attending",
                                   "created encounter"), (attending.id, "attending", "assigned attending")):
        if not db.scalars(select(CareTeamAssignment).where(CareTeamAssignment.user_id == user_id,
                                                           CareTeamAssignment.patient_id == patient.id,
                                                           CareTeamAssignment.revoked_at.is_(None))).first():
            db.add(CareTeamAssignment(patient_id=patient.id, user_id=user_id, role=role, reason=reason,
                                      granted_by=principal.user_id))
            audit.record(db, action="care_team.granted", category="access", outcome="success", patient_id=patient.id,
                         encounter_id=e.id, resource_type="care_team", resource_id=user_id,
                         details={"role": role, "reason_code": "encounter"}, **principal.audit_ctx())
    audit.record(db, action="encounter.create", category="modification", outcome="success", patient_id=patient.id,
                 encounter_id=e.id, resource_type="encounter", resource_id=e.id,
                 details={"encounter_class": body.encounter_class}, **principal.audit_ctx())
    db.commit()
    return _detail(db, e)


@router.get("/{encounter_id}")
def detail(encounter_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
           db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "encounter.read")
    audit.record(db, action="encounter.read", category="access", outcome="allowed", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="encounter", resource_id=e.id, **principal.audit_ctx())
    db.commit()
    return _detail(db, e)


@router.post("/{encounter_id}/verify-identity")
def verify_identity(encounter_id: uuid.UUID, body: VerifyIdentityIn,
                    principal: Principal = Depends(require("intake.record")), db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "intake.record")
    patient = e.patient
    mrn_ok = body.mrn.strip().upper() == patient.mrn.upper()
    dob_ok = body.birth_date == patient.birth_date
    if not (mrn_ok and dob_ok):
        other = db.scalars(select(Patient).where(Patient.mrn == body.mrn.strip().upper())).first()
        audit.record_now(action="identity.mismatch", category="safety", outcome="denied", patient_id=patient.id,
                         encounter_id=e.id, resource_type="encounter", resource_id=e.id,
                         details={"mrn_match": mrn_ok, "dob_match": dob_ok,
                                  "mrn_belongs_to_other_patient": bool(other and other.id != patient.id)},
                         **principal.audit_ctx())
        raise HTTPException(409, detail={
            "code": "identity_mismatch",
            "message": "The identifiers entered do not match this encounter's patient. Stop and re-check the "
                       "wristband or ID before continuing.",
            "mrn_match": mrn_ok, "dob_match": dob_ok})
    e.identity_verified_by, e.identity_verified_at, e.identity_method = principal.user_id, utcnow(), body.method
    e.version += 1
    audit.record(db, action="identity.verified", category="safety", outcome="success", patient_id=patient.id,
                 encounter_id=e.id, resource_type="encounter", resource_id=e.id, details={"method": body.method},
                 **principal.audit_ctx())
    db.commit()
    return _detail(db, e)


@router.post("/{encounter_id}/consents", status_code=201)
def record_consent(encounter_id: uuid.UUID, body: ConsentIn, principal: Principal = Depends(require("consent.record")),
                   db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "consent.record")
    if e.status in ("finalized", "cancelled", "entered_in_error"):
        raise HTTPException(409, detail={"code": "encounter_closed"})
    rec = ConsentRecord(patient_id=e.patient_id, encounter_id=e.id, consent_type=body.consent_type,
                        status=body.status, method=body.method, statement_version=body.statement_version,
                        recorded_by=principal.user_id)
    db.add(rec)
    audit.record(db, action="consent.recorded", category="policy", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="consent", details={"consent_type": body.consent_type,
                                                                      "status": body.status, "method": body.method},
                 **principal.audit_ctx())
    db.commit()
    return {"consent": consent_state(db, e)}


def require_ready_for_intake(db: Session, e: Encounter) -> None:
    """Gate used by intake/workflow routes: verified identity and granted recording + AI-processing consent."""
    if e.identity_verified_at is None:
        raise HTTPException(409, detail={"code": "identity_not_verified",
                                         "message": "Verify the patient's identity before intake."})
    state = consent_state(db, e)
    missing = [t for t in ("audio_recording", "ai_processing") if state.get(t, {}).get("status") != "granted"]
    if missing:
        raise HTTPException(409, detail={"code": "consent_required", "missing": missing,
                                         "message": "Record the patient's consent before intake."})


__all__ = ["router", "get_scoped_encounter", "require_ready_for_intake", "has_patient_scope"]
