"""Allow-list serializers for clinical data (guardrail 3: data minimization).

Each function builds its output field by field from the ORM row. Contact details and administrative
identifiers have no serializer here at all, so they cannot leak into clinical views by accident.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from ..models import (Allergy, ClinicalNote, Condition, Encounter, MedicalDocument, MedicationStatement, Patient,
                      Procedure, Symptom, VitalSign)

RECONCILIATION_FRESH = timedelta(days=180)
CURRENT_MED_STATUSES = {"active", "on_hold", "intended"}
HISTORICAL_MED_STATUSES = {"completed", "stopped", "not_taken"}


def age_on(birth: date, on: date | None = None) -> int:
    on = on or date.today()
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def provenance(row) -> dict:
    return {"source_kind": row.source_kind, "source_ref": row.source_ref, "source_phrase": row.source_phrase,
            "certainty": row.certainty, "status": row.status, "recorded_at": row.recorded_at}


def patient_summary(p: Patient, *, in_care_team: bool) -> dict:
    return {"id": str(p.id), "display_name": p.display_name, "mrn": p.mrn, "birth_date": p.birth_date,
            "age": age_on(p.birth_date), "sex_at_birth": p.sex_at_birth, "in_care_team": in_care_team,
            "is_synthetic": p.is_synthetic}


def encounter_view(e: Encounter, *, attending_name: str | None = None) -> dict:
    return {"id": str(e.id), "patient_id": str(e.patient_id), "encounter_class": e.encounter_class,
            "status": e.status, "chief_complaint": e.chief_complaint, "location": e.location,
            "attending_user_id": str(e.attending_user_id) if e.attending_user_id else None,
            "attending_name": attending_name, "started_at": e.started_at, "ended_at": e.ended_at,
            "identity_verified": e.identity_verified_at is not None, "identity_method": e.identity_method,
            "identity_verified_at": e.identity_verified_at, "version": e.version}


def condition_view(c: Condition) -> dict:
    return {"id": str(c.id), "encounter_id": str(c.encounter_id) if c.encounter_id else None, "name": c.name,
            "code": c.code, "clinical_status": c.clinical_status, "verification_status": c.verification_status,
            "onset_date": c.onset_date, "abatement_date": c.abatement_date, **provenance(c)}


def medication_category(m: MedicationStatement) -> str:
    if m.med_status in CURRENT_MED_STATUSES:
        return "current"
    if m.med_status in HISTORICAL_MED_STATUSES:
        return "historical"
    return "status_unknown"


def reconciliation_state(m: MedicationStatement, now: datetime | None = None) -> str:
    if m.last_reconciled_at is None:
        return "never_reconciled"
    now = now or datetime.now(UTC)
    return "recent" if now - m.last_reconciled_at <= RECONCILIATION_FRESH else "stale"


def medication_view(m: MedicationStatement) -> dict:
    return {"id": str(m.id), "medication_text": m.medication_text, "medication_id": str(m.medication_id)
            if m.medication_id else None, "dose_text": m.dose_text, "route": m.route,
            "frequency_text": m.frequency_text, "med_status": m.med_status, "category": medication_category(m),
            "effective_start": m.effective_start, "effective_end": m.effective_end,
            "last_reconciled_at": m.last_reconciled_at, "reconciliation": reconciliation_state(m),
            **provenance(m)}


def allergy_view(a: Allergy) -> dict:
    return {"id": str(a.id), "substance_text": a.substance_text, "category": a.category,
            "reaction_text": a.reaction_text, "reaction_severity": a.reaction_severity, "criticality": a.criticality,
            "clinical_status": a.clinical_status, "verification_status": a.verification_status, **provenance(a)}


def symptom_view(s: Symptom) -> dict:
    return {"id": str(s.id), "encounter_id": str(s.encounter_id), "name": s.name, "negated": s.negated,
            "onset_text": s.onset_text, "duration_text": s.duration_text, "severity_text": s.severity_text,
            **provenance(s)}


def procedure_view(p: Procedure) -> dict:
    return {"id": str(p.id), "name": p.name, "performed_date": p.performed_date, "performed_text": p.performed_text,
            **provenance(p)}


def vital_view(v: VitalSign) -> dict:
    return {"id": str(v.id), "encounter_id": str(v.encounter_id), "kind": v.kind, "value": float(v.value),
            "unit": v.unit, "measured_at": v.measured_at, "recorded_by": str(v.recorded_by)}


def note_view(n: ClinicalNote, *, author_name: str | None = None, body: str | None = None) -> dict:
    out = {"id": str(n.id), "encounter_id": str(n.encounter_id), "note_type": n.note_type, "title": n.title,
           "status": n.status, "current_version": n.current_version, "author_name": author_name,
           "finalized_at": n.finalized_at, "created_at": n.created_at,
           "source_ref": f"note:{n.id}@v{n.current_version}"}
    if body is not None:
        out["body"] = body
    return out


def document_view(d: MedicalDocument, version=None) -> dict:
    out = {"id": str(d.id), "encounter_id": str(d.encounter_id) if d.encounter_id else None,
           "document_type": d.document_type, "title": d.title, "clinical_date": d.clinical_date,
           "source_name": d.source_name, "status": d.status, "current_version": d.current_version,
           "created_at": d.created_at}
    if version is not None:
        out.update({"version": version.version, "mime_type": version.mime_type, "size_bytes": version.size_bytes,
                    "page_count": version.page_count, "content_sha256": version.content_sha256,
                    "ingestion_status": version.ingestion_status, "ingestion_error": version.ingestion_error,
                    "embedding_model": version.embedding_model, "indexed_at": version.indexed_at,
                    "phi_scan": version.phi_scan, "source_ref": f"document:{d.id}@v{version.version}"})
    return out
