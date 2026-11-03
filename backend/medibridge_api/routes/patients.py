"""Patient registry and patient-scoped clinical record."""
from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (Allergy, CareTeamAssignment, ClinicalNote, ClinicalNoteVersion, Condition, DocumentVersion,
                      Encounter, MedicalDocument, MedicationStatement, Patient, PatientContact, PatientIdentifier,
                      Procedure, Symptom, User, VitalSign)
from ..security.deps import Principal, ensure_patient_scope, require
from ..services import audit, clinical_views as views

router = APIRouter(prefix="/api/patients", tags=["patients"])

MRN_RE = re.compile(r"^MB\d{6}$", re.I)


def _patient_or_404(db: Session, patient_id: uuid.UUID) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    return p


def _scoped_patient(db: Session, principal: Principal, patient_id: uuid.UUID, permission: str) -> Patient:
    ensure_patient_scope(db, principal, patient_id, permission)  # 404 + audit when out of scope
    return _patient_or_404(db, patient_id)


def _read_audit(db: Session, principal: Principal, patient: Patient, resource: str, **details) -> None:
    audit.record(db, action="patient.read", category="access", outcome="allowed", patient_id=patient.id,
                 resource_type=resource, details=details or {}, **principal.audit_ctx())
    db.commit()


def _care_team_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    return set(db.scalars(select(CareTeamAssignment.patient_id).where(CareTeamAssignment.user_id == user_id,
                                                                       CareTeamAssignment.revoked_at.is_(None))))


@router.get("")
def search(q: str = Query("", max_length=80), scope: str = Query("all", pattern="^(all|mine)$"),
           limit: int = Query(25, ge=1, le=100), principal: Principal = Depends(require("registry.search")),
           db: Session = Depends(get_db)) -> dict:
    """Registry search returns minimal demographics only (name, DOB, MRN, sex)."""
    mine = _care_team_ids(db, principal.user_id)
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    term = q.strip()
    kind = "list"
    if term:
        if MRN_RE.match(term):
            kind, stmt = "mrn", stmt.where(func.upper(Patient.mrn) == term.upper())
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", term):
            kind, stmt = "dob", stmt.where(Patient.birth_date == date.fromisoformat(term))
        else:
            if len(term) < 2:
                raise HTTPException(422, detail={"code": "query_too_short", "message": "Type at least 2 characters."})
            kind = "name"
            like = f"{term.lower()}%"
            parts = term.lower().split()
            conds = [func.lower(Patient.family_name).like(like), func.lower(Patient.given_name).like(like)]
            if len(parts) >= 2:
                conds.append((func.lower(Patient.given_name).like(f"{parts[0]}%"))
                             & (func.lower(Patient.family_name).like(f"{parts[-1]}%")))
            stmt = stmt.where(or_(*conds))
    if scope == "mine":
        stmt = stmt.where(Patient.id.in_(mine) if mine else false())
    rows = db.scalars(stmt.order_by(Patient.family_name, Patient.given_name).limit(limit)).all()
    audit.record(db, action="registry.search", category="access", outcome="allowed", resource_type="registry",
                 details={"query_kind": kind, "scope": scope, "result_count": len(rows)}, **principal.audit_ctx())
    db.commit()
    return {"items": [views.patient_summary(p, in_care_team=p.id in mine) for p in rows]}


@router.get("/{patient_id}")
def profile(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
            db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    counts = {
        "encounters": db.scalar(select(func.count()).select_from(Encounter).where(Encounter.patient_id == p.id)),
        "documents": db.scalar(select(func.count()).select_from(MedicalDocument)
                               .where(MedicalDocument.patient_id == p.id, MedicalDocument.deleted_at.is_(None))),
        "active_conditions": db.scalar(select(func.count()).select_from(Condition)
                                       .where(Condition.patient_id == p.id, Condition.clinical_status == "active",
                                              Condition.status != "entered_in_error")),
        "allergies": db.scalar(select(func.count()).select_from(Allergy)
                               .where(Allergy.patient_id == p.id, Allergy.status != "entered_in_error")),
    }
    _read_audit(db, principal, p, "patient_profile")
    return {**views.patient_summary(p, in_care_team=True), "counts": counts}


@router.get("/{patient_id}/admin-identifiers")
def admin_identifiers(patient_id: uuid.UUID, reason: str = Query(..., min_length=5, max_length=200),
                      principal: Principal = Depends(require("registry.read_admin_identifiers")),
                      db: Session = Depends(get_db)) -> dict:
    """Administrative identifiers and contacts: administrators only, with a recorded reason."""
    p = _patient_or_404(db, patient_id)
    ids = db.scalars(select(PatientIdentifier).where(PatientIdentifier.patient_id == p.id)).all()
    contacts = db.scalars(select(PatientContact).where(PatientContact.patient_id == p.id)).all()
    audit.record(db, action="patient.admin_identifiers.read", category="access", outcome="allowed",
                 patient_id=p.id, resource_type="patient_identifiers", details={"reason_given": True,
                                                                                "reason_length": len(reason)},
                 **principal.audit_ctx())
    db.commit()
    return {"patient": {"id": str(p.id), "display_name": p.display_name, "mrn": p.mrn},
            "identifiers": [{"system": i.system, "value": i.value} for i in ids],
            "contacts": [{"kind": c.kind, "value": c.value} for c in contacts]}


@router.get("/{patient_id}/timeline")
def timeline(patient_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
             db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "encounter.read")
    encounters = db.scalars(select(Encounter).where(Encounter.patient_id == p.id)
                            .order_by(Encounter.started_at.desc())).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    note_counts = dict(db.execute(select(ClinicalNote.encounter_id, func.count()).where(
        ClinicalNote.patient_id == p.id).group_by(ClinicalNote.encounter_id)).all())
    doc_counts = dict(db.execute(select(MedicalDocument.encounter_id, func.count()).where(
        MedicalDocument.patient_id == p.id, MedicalDocument.deleted_at.is_(None))
        .group_by(MedicalDocument.encounter_id)).all())
    _read_audit(db, principal, p, "timeline", encounters=len(encounters))
    return {"items": [{**views.encounter_view(e, attending_name=names.get(e.attending_user_id)),
                       "note_count": note_counts.get(e.id, 0), "document_count": doc_counts.get(e.id, 0)}
                      for e in encounters]}


@router.get("/{patient_id}/conditions")
def conditions(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
               db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    rows = db.scalars(select(Condition).where(Condition.patient_id == p.id).order_by(Condition.onset_date.desc())).all()
    procs = db.scalars(select(Procedure).where(Procedure.patient_id == p.id)).all()
    _read_audit(db, principal, p, "conditions", count=len(rows))
    return {"items": [views.condition_view(c) for c in rows], "procedures": [views.procedure_view(x) for x in procs]}


@router.get("/{patient_id}/medications")
def medications(patient_id: uuid.UUID, principal: Principal = Depends(require("medication.read")),
                db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "medication.read")
    rows = db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id == p.id,
                                                         MedicationStatement.status != "entered_in_error")
                      .order_by(MedicationStatement.effective_start.desc().nulls_last())).all()
    items = [views.medication_view(m) for m in rows]
    current = [m for m in items if m["category"] == "current"]
    summary = {
        "current": len(current),
        "historical": sum(m["category"] == "historical" for m in items),
        "status_unknown": sum(m["category"] == "status_unknown" for m in items),
        "uncertain": sum(m["certainty"] != "stated" for m in items),
        "reconciliation": ("never_reconciled" if not current or any(m["reconciliation"] == "never_reconciled" for m in current)
                           else "stale" if any(m["reconciliation"] == "stale" for m in current) else "recent"),
    }
    _read_audit(db, principal, p, "medications", count=len(rows))
    return {"items": items, "summary": summary}


@router.get("/{patient_id}/allergies")
def allergies(patient_id: uuid.UUID, principal: Principal = Depends(require("medication.read")),
              db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "medication.read")
    rows = db.scalars(select(Allergy).where(Allergy.patient_id == p.id).order_by(Allergy.recorded_at.desc())).all()
    _read_audit(db, principal, p, "allergies", count=len(rows))
    return {"items": [views.allergy_view(a) for a in rows]}


@router.get("/{patient_id}/notes")
def notes(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
          db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    rows = db.scalars(select(ClinicalNote).where(ClinicalNote.patient_id == p.id)
                      .order_by(ClinicalNote.created_at.desc())).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    _read_audit(db, principal, p, "notes", count=len(rows))
    return {"items": [views.note_view(n, author_name=names.get(n.author_user_id)) for n in rows]}


@router.get("/{patient_id}/notes/{note_id}")
def note_detail(patient_id: uuid.UUID, note_id: uuid.UUID,
                principal: Principal = Depends(require("patient.read_clinical")), db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    n = db.get(ClinicalNote, note_id)
    if n is None or n.patient_id != p.id:
        raise HTTPException(404, detail={"code": "not_found"})
    versions = db.scalars(select(ClinicalNoteVersion).where(ClinicalNoteVersion.note_id == n.id)
                          .order_by(ClinicalNoteVersion.version)).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    _read_audit(db, principal, p, "note", note_id=str(n.id))
    return {**views.note_view(n, author_name=names.get(n.author_user_id), body=versions[-1].body if versions else ""),
            "versions": [{"version": v.version, "author_kind": v.author_kind, "created_by": names.get(v.created_by),
                          "created_at": v.created_at, "content_sha256": v.content_sha256,
                          "change_reason": v.change_reason} for v in versions]}


@router.get("/{patient_id}/symptoms")
def symptoms(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
             db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    rows = db.scalars(select(Symptom).where(Symptom.patient_id == p.id).order_by(Symptom.recorded_at.desc())).all()
    _read_audit(db, principal, p, "symptoms", count=len(rows))
    return {"items": [views.symptom_view(s) for s in rows]}


@router.get("/{patient_id}/vitals")
def vitals(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
           db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    rows = db.scalars(select(VitalSign).where(VitalSign.patient_id == p.id).order_by(VitalSign.measured_at.desc())
                      .limit(200)).all()
    _read_audit(db, principal, p, "vitals", count=len(rows))
    return {"items": [views.vital_view(v) for v in rows]}


@router.get("/{patient_id}/results")
def results(patient_id: uuid.UUID, principal: Principal = Depends(require("patient.read_clinical")),
            db: Session = Depends(get_db)) -> dict:
    """Cardiology results and vitals (FHIR Observation): latest, previous and the series for trends."""
    from ..models import Observation

    p = _scoped_patient(db, principal, patient_id, "patient.read_clinical")
    rows = db.scalars(select(Observation).where(Observation.patient_id == p.id, Observation.status == "final")
                      .order_by(Observation.effective_at)).all()
    series: dict[str, dict] = {}
    for o in rows:
        s = series.setdefault(o.name, {"name": o.name, "code": o.code, "unit": o.unit, "category": o.category, "points": []})
        s["points"].append({"date": o.effective_at.date().isoformat(),
                            "value": float(o.value_numeric) if o.value_numeric is not None else None})
    _read_audit(db, principal, p, "results", count=len(rows))
    return {"items": [{**s, "points": s["points"][-40:], "latest": s["points"][-1],
                       "previous": s["points"][-2] if len(s["points"]) > 1 else None, "count": len(s["points"])}
                      for s in series.values()]}


@router.get("/{patient_id}/documents")
def documents(patient_id: uuid.UUID, principal: Principal = Depends(require("document.read")),
              db: Session = Depends(get_db)) -> dict:
    p = _scoped_patient(db, principal, patient_id, "document.read")
    docs = db.scalars(select(MedicalDocument).where(MedicalDocument.patient_id == p.id,
                                                    MedicalDocument.deleted_at.is_(None))
                      .order_by(MedicalDocument.clinical_date.desc().nulls_last())).all()
    by_doc: dict = {}
    for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id.in_([d.id for d in docs]))):
        if v.document_id not in by_doc or v.version > by_doc[v.document_id].version:
            by_doc[v.document_id] = v
    _read_audit(db, principal, p, "documents", count=len(docs))
    return {"items": [views.document_view(d, by_doc.get(d.id)) for d in docs]}
