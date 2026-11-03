"""Patient-scoped clinical data access for agents and tools (deterministic; no LLM).

Every item returned carries an ``evidence_id`` and provenance so the handoff can cite it. Conflicts between
sources (guardrail 10) are detected by explicit rules and surfaced, never silently resolved.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from medibridge_api.models import (Allergy, ClinicalNote, ClinicalNoteVersion, Condition, Encounter, MedicalDocument,
                                   MedicationStatement, Patient, Prescription, Procedure, User)
from medibridge_api.services import clinical_views as views

NKDA_RE = re.compile(r"\b(no known (drug )?allergies|nkda|allergies:\s*(none|no known))", re.I)
MISSING_DOC_RE = re.compile(r"(?P<what>[A-Z][\w -]{2,40}?)\s+(?:was\s+)?performed at an outside facility;?\s*report requested"
                            r"|(?P<what2>[A-Z][\w -]{2,40}?)\s+report (?:requested|pending|not yet received)", re.I)


def _age_days(dt) -> int | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return (datetime.now(UTC) - dt).days
    return (datetime.now(UTC).date() - dt).days


def patient_header(db: Session, patient_id: uuid.UUID) -> dict:
    p = db.get(Patient, patient_id)
    return {"patient_id": str(p.id), "display_name": p.display_name, "mrn": p.mrn, "age": views.age_on(p.birth_date),
            "sex_at_birth": p.sex_at_birth, "is_synthetic": p.is_synthetic}


def encounter_header(db: Session, encounter_id: uuid.UUID) -> dict:
    e = db.get(Encounter, encounter_id)
    attending = db.get(User, e.attending_user_id) if e.attending_user_id else None
    return {"encounter_id": str(e.id), "encounter_class": e.encounter_class, "started_at": e.started_at.isoformat(),
            "chief_complaint_at_desk": e.chief_complaint, "attending": attending.display_name if attending else None,
            "location": e.location, "status": e.status}


# Cardiology results the Chart agent reports (latest value, the one before it, and how many exist). Values are
# shown exactly as recorded; no normal/abnormal interpretation is added here.
RESULT_ORDER = ("High-sensitivity troponin I", "NT-proBNP", "LV ejection fraction", "LDL cholesterol",
                "Total cholesterol", "HDL cholesterol", "Triglycerides", "Potassium", "Sodium", "Creatinine", "eGFR",
                "INR", "Prothrombin time", "HbA1c", "Hemoglobin", "Systolic blood pressure", "Diastolic blood pressure",
                "Heart rate", "Body weight", "BMI")
ENCOUNTER_LIMIT = 20


def results(db: Session, patient_id: uuid.UUID) -> list[dict]:
    from medibridge_api.models import Observation

    rows = db.scalars(select(Observation).where(Observation.patient_id == patient_id, Observation.status == "final")
                      .order_by(Observation.effective_at.desc())).all()
    by_name: dict[str, list] = {}
    for o in rows:
        by_name.setdefault(o.name, []).append(o)
    out = []
    for name in RESULT_ORDER:
        series = by_name.get(name)
        if not series:
            continue
        latest = series[0]

        def point(o):
            value = float(o.value_numeric) if o.value_numeric is not None else o.value_text
            return {"value": round(value, 2) if isinstance(value, float) else value, "unit": o.unit,
                    "date": o.effective_at.date().isoformat(), "ref": f"observation:{o.id}"}

        out.append({"evidence_id": f"result:{latest.id}", "name": name, "code": latest.code, "category": latest.category,
                    "latest": point(latest), "previous": point(series[1]) if len(series) > 1 else None,
                    "count": len(series), "age_days": _age_days(latest.effective_at)})
    return out


def history(db: Session, patient_id: uuid.UUID, current_encounter_id: uuid.UUID, *, note_limit: int = 5) -> dict:
    names = dict(db.execute(select(User.id, User.display_name)).all())
    encounters = db.scalars(select(Encounter).where(Encounter.patient_id == patient_id, Encounter.id != current_encounter_id)
                            .order_by(Encounter.started_at.desc())).all()
    conditions = db.scalars(select(Condition).where(Condition.patient_id == patient_id,
                                                    Condition.status != "entered_in_error")).all()
    meds = db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id == patient_id,
                                                         MedicationStatement.status != "entered_in_error")).all()
    allergies = db.scalars(select(Allergy).where(Allergy.patient_id == patient_id, Allergy.status != "entered_in_error")).all()
    prescriptions = db.scalars(select(Prescription).where(Prescription.patient_id == patient_id)
                               .order_by(Prescription.authored_at.desc())).all()
    procedures = db.scalars(select(Procedure).where(Procedure.patient_id == patient_id)).all()
    notes = db.scalars(select(ClinicalNote).where(ClinicalNote.patient_id == patient_id,
                                                  ClinicalNote.status.in_(("final", "amended")))
                       .order_by(ClinicalNote.created_at.desc()).limit(note_limit)).all()
    bodies = {v.note_id: v for v in db.scalars(select(ClinicalNoteVersion).where(
        ClinicalNoteVersion.note_id.in_([n.id for n in notes])))}
    enc_by_id = {e.id: e for e in encounters}

    def ref(kind: str, row) -> str:
        return f"{kind}:{row.id}"

    out = {
        "prior_encounters": [{"evidence_id": f"encounter:{e.id}", "date": e.started_at.date().isoformat(),
                              "reason": e.chief_complaint, "class": e.encounter_class, "status": e.status,
                              "attending": names.get(e.attending_user_id), "age_days": _age_days(e.started_at)}
                             for e in encounters[:ENCOUNTER_LIMIT]],
        "prior_encounters_total": len(encounters),
        "results": results(db, patient_id),
        "conditions": [{"evidence_id": ref("condition", c), "name": c.name, "clinical_status": c.clinical_status,
                        "verification_status": c.verification_status,
                        "onset_date": c.onset_date.isoformat() if c.onset_date else None, "source_kind": c.source_kind,
                        "source_ref": c.source_ref, "recorded_at": c.recorded_at.isoformat(),
                        "age_days": _age_days(c.recorded_at)} for c in conditions],
        "medications": [{"evidence_id": ref("medication_statement", m), "text": m.medication_text, "dose": m.dose_text,
                         "frequency": m.frequency_text, "status": m.med_status, "category": views.medication_category(m),
                         "reconciliation": views.reconciliation_state(m),
                         "last_reconciled_at": m.last_reconciled_at.isoformat() if m.last_reconciled_at else None,
                         "effective_end": m.effective_end.isoformat() if m.effective_end else None,
                         "source_kind": m.source_kind, "certainty": m.certainty, "source_phrase": m.source_phrase,
                         "source_ref": m.source_ref} for m in meds],
        "allergies": [{"evidence_id": ref("allergy", a), "substance": a.substance_text, "reaction": a.reaction_text,
                       "criticality": a.criticality, "verification_status": a.verification_status,
                       "clinical_status": a.clinical_status, "source_kind": a.source_kind, "source_ref": a.source_ref,
                       "recorded_at": a.recorded_at.isoformat(), "age_days": _age_days(a.recorded_at)} for a in allergies],
        "prescriptions": [{"evidence_id": ref("prescription", r), "medication": r.medication_text, "dose": r.dose_text,
                           "frequency": r.frequency_text, "status": r.status, "authored_at": r.authored_at.isoformat(),
                           "prescriber": names.get(r.prescriber_user_id), "age_days": _age_days(r.authored_at),
                           "note": "historical order - not evidence of current use" if r.status != "active" else None}
                          for r in prescriptions],
        "procedures": [{"evidence_id": ref("procedure", p), "name": p.name, "performed": p.performed_text
                        or (p.performed_date.isoformat() if p.performed_date else None), "source_kind": p.source_kind}
                       for p in sorted(procedures, key=lambda x: x.performed_date or date.min, reverse=True)],
        "recent_notes": [{"evidence_id": f"note:{n.id}@v{n.current_version}", "date": n.created_at.date().isoformat(),
                          "title": n.title, "author": names.get(n.author_user_id),
                          "encounter_reason": enc_by_id[n.encounter_id].chief_complaint if n.encounter_id in enc_by_id else None,
                          "text": bodies[n.id].body if n.id in bodies else "", "age_days": _age_days(n.created_at)}
                         for n in notes],
    }
    out["conflicts"] = detect_conflicts(db, patient_id, out)
    out["missing_documents"] = missing_documents(db, patient_id, out["recent_notes"])
    current = [m for m in out["medications"] if m["category"] == "current"]
    out["medication_list_state"] = ("none_recorded" if not current else
                                    "stale" if any(m["reconciliation"] != "recent" for m in current) else "recent")
    return out


def detect_conflicts(db: Session, patient_id: uuid.UUID, h: dict) -> list[dict]:
    conflicts = []
    confirmed = [a for a in h["allergies"] if a["verification_status"] in ("confirmed", "unconfirmed", "presumed")
                 and a["clinical_status"] == "active"]
    if confirmed:
        nkda_notes = [n for n in h["recent_notes"] if NKDA_RE.search(n["text"])]
        docs = db.scalars(select(MedicalDocument).where(MedicalDocument.patient_id == patient_id,
                                                        MedicalDocument.document_type == "medication_list")).all()
        from medibridge_api.models import DocumentVersion

        nkda_docs = []
        for d in docs:
            v = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d.id,
                                                         DocumentVersion.version == d.current_version)).first()
            if v and v.extracted_text and NKDA_RE.search(v.extracted_text):
                nkda_docs.append({"evidence_id": f"document:{d.id}@v{v.version}", "date": d.clinical_date.isoformat() if d.clinical_date else None,
                                  "title": d.title})
        for a in confirmed:
            sources = [{"evidence_id": n["evidence_id"], "date": n["date"], "title": n["title"]} for n in nkda_notes] + nkda_docs
            if sources:
                conflicts.append({"kind": "allergy_vs_nkda", "field": "allergies", "subject": a["substance"],
                                  "summary": f"An allergy to {a['substance']} is recorded, but "
                                             f"{len(sources)} later record(s) state no known drug allergies.",
                                  "evidence_ids": [a["evidence_id"], *[s["evidence_id"] for s in sources]],
                                  "resolution": "unresolved - clinician must confirm"})
    for m in h["medications"]:
        if m["category"] == "status_unknown" or m["certainty"] != "stated":
            conflicts.append({"kind": "medication_uncertain", "field": "medications", "subject": m["text"],
                              "summary": f"Medication '{m['text']}' has unknown status or an uncertain name.",
                              "evidence_ids": [m["evidence_id"]], "resolution": "unresolved - confirm with patient/pharmacy"})
    return conflicts


def missing_documents(db: Session, patient_id: uuid.UUID, notes: list[dict]) -> list[dict]:
    """Reports referenced in notes that are not on file (distinct from 'no abnormality found')."""
    titles = [d.title.lower() for d in db.scalars(select(MedicalDocument).where(MedicalDocument.patient_id == patient_id))]
    out = []
    for n in notes:
        for m in MISSING_DOC_RE.finditer(n["text"]):
            what = (m.group("what") or m.group("what2") or "").strip()
            if what and not any(what.lower() in t for t in titles):
                out.append({"what": what, "referenced_in": n["evidence_id"], "date": n["date"],
                            "summary": f"{what} is referenced in a note ({n['date']}) but no report is on file."})
    return out


def patient_reported_vs_record(findings: dict, h: dict) -> list[dict]:
    """Conflicts between what the patient said today and the record (e.g. 'no allergies' vs recorded allergy)."""
    out = []
    denial = findings.get("allergy_denial")
    recorded = [a for a in h["allergies"] if a["clinical_status"] == "active" and a["verification_status"] != "refuted"]
    if denial in ("denied", "uncertain") and recorded:
        out.append({"kind": "intake_allergy_denial_vs_record", "field": "allergies",
                    "subject": ", ".join(a["substance"] for a in recorded),
                    "summary": ("The patient " + ("denied" if denial == "denied" else "was unsure about")
                                + " allergies at intake, but the record lists: "
                                + ", ".join(a["substance"] for a in recorded) + "."),
                    "evidence_ids": [findings.get("allergy_denial_ref") or "intake", *[a["evidence_id"] for a in recorded]],
                    "resolution": "unresolved - clinician must confirm"})
    return out
