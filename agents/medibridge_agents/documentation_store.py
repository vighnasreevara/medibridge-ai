"""Clinical documentation: prepare (validate) and commit (one transaction) a doctor-approved change set.

Flow: the doctor prepares the note and orders (``prepare``: structure only), signs the exact change set, the
Order Entry agent has the Medication Safety agent screen the proposed orders (``record_screen``), the doctor
overrides or revises any blocking alert (``record_decision``), and the Chart agent commits (``commit``).

Guardrails enforced here:
- 6: prescriptions are only ever doctor-entered; there is no code path from model output to a prescription;
- 7: commit requires the prepared change set's exact hash, an approved handoff, and an active doctor;
- 5: commit requires a medication-safety screen of this exact change set; blocking alerts need the doctor's
  recorded override (with a reason) - screening informs, the doctor decides;
- 3: the note text must not contain contact/administrative identifiers;
- finalized records are written once; the database triggers prevent silent edits afterwards.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from medibridge_api.guardrails import phi
from medibridge_api.models import (Allergy, ApprovalRecord, ClinicalNote, ClinicalNoteVersion, DocumentationChangeSet,
                                   Encounter, HandoffVersion, MedicationStatement, Prescription, Symptom, User)
from medibridge_api.models.base import utcnow
from medibridge_api.services import audit, events, outbox

from . import handoff_store

# Alerts that send the orders back to the doctor; "moderate", "minor" and "caution" are shown but do not stop.
BLOCKING_SEVERITIES = ("contraindicated", "major")


class PrescriptionIn(BaseModel):
    medication_text: str = Field(min_length=2, max_length=300)
    dose_text: str = Field(min_length=1, max_length=120)
    route: str = Field(min_length=2, max_length=60)
    frequency_text: str = Field(min_length=2, max_length=120)
    duration_text: str = Field("", max_length=120)
    quantity_text: str = Field("", max_length=120)
    refills: int = Field(0, ge=0, le=12)
    instructions: str = Field("", max_length=1000)


class DocumentationIn(BaseModel):
    note_title: str = Field("Intake and assessment", max_length=200)
    subjective: str = Field("", max_length=8000)
    objective: str = Field("", max_length=4000)
    assessment: str = Field(min_length=3, max_length=8000)
    plan: str = Field(min_length=3, max_length=8000)
    record_findings: list[str] = Field(default_factory=list, max_length=60)  # intake evidence ids to add to record
    prescriptions: list[PrescriptionIn] = Field(default_factory=list, max_length=10)


class DocumentationInvalid(Exception):
    def __init__(self, problems: list[str], preview: dict | None = None):
        super().__init__("; ".join(problems))
        self.problems, self.preview = problems, preview


def _sha(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def note_body(d: DocumentationIn) -> str:
    parts = [("Subjective (patient-reported at intake)", d.subjective), ("Objective", d.objective),
             ("Assessment (doctor)", d.assessment), ("Plan (doctor)", d.plan)]
    return "\n\n".join(f"{h}:\n{t.strip()}" for h, t in parts if t.strip())


def prepare(db: Session, *, encounter_id: uuid.UUID, run_id: uuid.UUID | None, doctor_id: uuid.UUID,
            data: DocumentationIn) -> DocumentationChangeSet:
    enc = db.get(Encounter, encounter_id)
    problems: list[str] = []
    if enc.status in ("finalized", "cancelled", "entered_in_error"):
        raise DocumentationInvalid([f"The encounter is {enc.status}."])
    report = handoff_store.get_report(db, encounter_id)
    if report is None or report.status != "approved":
        raise DocumentationInvalid(["The handoff must be approved by a doctor before documentation is prepared."])
    version = db.scalars(select(HandoffVersion).where(HandoffVersion.handoff_id == report.id,
                                                      HandoffVersion.version == report.approved_version)).one()
    bundle = version.evidence_bundle
    body = note_body(data)
    found = phi.contains_identifiers(body)
    if found:
        problems.append(f"Remove contact/administrative identifiers from the note ({', '.join(found)}).")
    facts = []
    for eid in dict.fromkeys(data.record_findings):
        item = bundle.get(eid)
        if not item or not eid.startswith("intake:"):
            problems.append(f"Finding {eid} is not part of the approved handoff.")
            continue
        facts.append({"evidence_id": eid, **item})

    rx_out = [p.model_dump() for p in data.prescriptions]
    content = {"encounter_id": str(encounter_id), "patient_id": str(enc.patient_id),
               "handoff_version_id": str(version.id), "handoff_sha256": version.content_sha256,
               "note": {"title": data.note_title, "body": body, "plan": data.plan.strip()}, "facts": facts,
               "prescriptions": rx_out}
    if problems:
        raise DocumentationInvalid(problems)
    for old in db.scalars(select(DocumentationChangeSet).where(DocumentationChangeSet.encounter_id == encounter_id,
                                                               DocumentationChangeSet.status == "prepared")):
        old.status = "superseded"
    cs = DocumentationChangeSet(id=uuid.uuid4(), run_id=run_id, encounter_id=encounter_id, patient_id=enc.patient_id,
                                handoff_version_id=version.id, content=content, content_sha256=_sha(content),
                                status="prepared", validation={"facts": len(facts), "prescriptions": len(rx_out)},
                                prepared_by=doctor_id)
    db.add(cs)
    return cs


def blocking(screen: dict | None) -> list[dict]:
    return [a for a in (screen or {}).get("alerts", []) if a["severity"] in BLOCKING_SEVERITIES]


def record_screen(db: Session, *, changeset_id: uuid.UUID, expected_sha256: str, screen: dict) -> DocumentationChangeSet:
    """Attach the Medication Safety result to the exact change set the doctor signed."""
    cs = db.get(DocumentationChangeSet, changeset_id, with_for_update=True)
    if cs is None or cs.status != "prepared" or cs.content_sha256 != expected_sha256:
        raise DocumentationInvalid(["The signed change set is no longer the prepared one; prepare it again."])
    cs.safety_screen = {**screen, "content_sha256": expected_sha256}
    cs.safety_decision = None
    return cs


def record_decision(db: Session, *, changeset_id: uuid.UUID, doctor_id: uuid.UUID, action: str, reason: str,
                    acknowledged: list[str], audit_ctx: dict) -> DocumentationChangeSet:
    """The doctor's answer to blocking alerts: override (every blocking alert acknowledged, with a reason) or revise."""
    cs = db.get(DocumentationChangeSet, changeset_id, with_for_update=True)
    if cs is None or cs.status != "prepared" or not cs.safety_screen:
        raise DocumentationInvalid(["There is no screened change set awaiting a safety decision."])
    doctor = db.get(User, doctor_id)
    if doctor is None or doctor.status != "active" or doctor.role.key != "doctor":
        raise DocumentationInvalid(["Only an active doctor can decide on medication-safety alerts."])
    ids = [a["id"] for a in blocking(cs.safety_screen)]
    if action == "override":
        missing = [i for i in ids if i not in acknowledged]
        if missing or len(reason.strip()) < 10:
            raise DocumentationInvalid(["Acknowledge every blocking alert and give a clinical reason (10+ characters)."])
        approval = ApprovalRecord(id=uuid.uuid4(), patient_id=cs.patient_id, encounter_id=cs.encounter_id,
                                  subject_type="safety_override", subject_id=cs.id, subject_sha256=cs.content_sha256,
                                  decision="approved", rationale=reason.strip()[:1000], decided_by=doctor_id,
                                  decided_role="doctor")
        db.add(approval)
        cs.safety_decision = {"action": "override", "reason": reason.strip()[:1000], "acknowledged": ids,
                              "approval_record_id": str(approval.id), "by": str(doctor_id), "at": utcnow().isoformat()}
    else:
        cs.status = "superseded"
        cs.safety_decision = {"action": "revise", "by": str(doctor_id), "at": utcnow().isoformat()}
    audit.record(db, action=f"medication_safety.{action}", category="safety", outcome="success",
                 patient_id=cs.patient_id, encounter_id=cs.encounter_id, resource_type="documentation_changeset",
                 resource_id=cs.id, details={"blocking_alerts": len(ids), "alert_ids": ids[:20]}, **audit_ctx)
    return cs


def commit(db: Session, *, changeset_id: uuid.UUID, expected_sha256: str, doctor_id: uuid.UUID, audit_ctx: dict,
           run_id: uuid.UUID | None = None) -> dict:
    """Write everything in the caller's transaction. Any failure rolls the whole change set back."""
    cs = db.get(DocumentationChangeSet, changeset_id, with_for_update=True)
    if cs is None or cs.status != "prepared":
        raise DocumentationInvalid(["The change set is not awaiting commit (already committed or superseded)."])
    if cs.content_sha256 != expected_sha256 or _sha(cs.content) != expected_sha256:
        raise DocumentationInvalid(["The change set changed after it was prepared; prepare it again."])
    screen = cs.safety_screen or {}
    if screen.get("content_sha256") != expected_sha256 or screen.get("status") not in ("clear", "alerts"):
        raise DocumentationInvalid(["The orders have not been screened by Medication Safety."])
    if blocking(screen) and (cs.safety_decision or {}).get("action") != "override":
        raise DocumentationInvalid(["Blocking medication-safety alerts need the doctor's override or revised orders."])
    doctor = db.get(User, doctor_id)
    if doctor is None or doctor.status != "active" or doctor.role.key != "doctor":
        raise DocumentationInvalid(["Only an active doctor can commit clinical documentation."])
    # Lock only the encounter row (its eager-loaded patient join cannot be locked FOR UPDATE).
    enc = db.scalars(select(Encounter).where(Encounter.id == cs.encounter_id).with_for_update(of=Encounter)).one()
    if enc.status == "finalized":
        raise DocumentationInvalid(["The encounter is already finalized."])
    c = cs.content
    now = utcnow()
    bundle_approval = ApprovalRecord(id=uuid.uuid4(), patient_id=cs.patient_id, encounter_id=cs.encounter_id,
                                     subject_type="documentation_bundle", subject_id=cs.id, subject_sha256=expected_sha256,
                                     decision="approved", rationale="Documentation committed by doctor.",
                                     decided_by=doctor_id, decided_role="doctor")
    db.add(bundle_approval)
    db.flush()
    note = ClinicalNote(id=uuid.uuid4(), patient_id=cs.patient_id, encounter_id=cs.encounter_id, note_type="intake_note",
                        title=c["note"]["title"], status="final", author_user_id=doctor_id, finalized_at=now,
                        finalized_by=doctor_id, approval_record_id=bundle_approval.id)
    db.add(note)
    db.flush()
    body = c["note"]["body"]
    db.add(ClinicalNoteVersion(note_id=note.id, version=1, body=body, content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                               author_kind="mixed", created_by=doctor_id, change_reason="Committed from approved handoff"))
    counts = {"symptoms": 0, "medications": 0, "allergies": 0, "prescriptions": 0}
    for f in c["facts"]:
        common = {"patient_id": cs.patient_id, "source_kind": "patient_reported", "source_ref": f.get("ref") or f["evidence_id"],
                  "source_phrase": f.get("source_phrase", ""), "recorded_by": doctor_id, "status": "confirmed"}
        if f["evidence_id"].startswith("intake:symptom"):
            db.add(Symptom(encounter_id=cs.encounter_id, name=f["label"], negated=bool(f.get("negated")),
                           duration_text=f.get("duration"), certainty="stated", **common))
            counts["symptoms"] += 1
        elif f["evidence_id"].startswith("intake:medication"):
            db.add(MedicationStatement(encounter_id=cs.encounter_id, medication_text=f["label"], dose_text=f.get("dose"),
                                       frequency_text=f.get("frequency"),
                                       med_status="active" if f.get("taking_now") == "yes" else "unknown",
                                       certainty=f.get("certainty", "stated"), effective_start=date.today(), **common))
            counts["medications"] += 1
        elif f["evidence_id"].startswith("intake:allergy"):
            db.add(Allergy(encounter_id=cs.encounter_id, substance_text=f["label"], reaction_text=f.get("reaction"),
                           verification_status="unconfirmed", certainty="stated", **common))
            counts["allergies"] += 1
    for p in c["prescriptions"]:
        rx_id = uuid.uuid4()
        rx_sha = hashlib.sha256(json.dumps({k: p[k] for k in ("medication_text", "dose_text", "route", "frequency_text")},
                                           sort_keys=True).encode()).hexdigest()
        approval = ApprovalRecord(id=uuid.uuid4(), patient_id=cs.patient_id, encounter_id=cs.encounter_id,
                                  subject_type="prescription", subject_id=rx_id, subject_sha256=rx_sha, decision="approved",
                                  rationale="Doctor-entered prescription committed with documentation.",
                                  decided_by=doctor_id, decided_role="doctor")
        db.add(approval)
        db.flush()
        db.add(Prescription(id=rx_id, patient_id=cs.patient_id, encounter_id=cs.encounter_id,
                            medication_text=p["medication_text"], dose_text=p["dose_text"], route=p["route"],
                            frequency_text=p["frequency_text"], duration_text=p.get("duration_text", ""),
                            quantity_text=p.get("quantity_text", ""), refills=p.get("refills", 0),
                            instructions=p.get("instructions", ""), status="active", prescriber_user_id=doctor_id,
                            approval_record_id=approval.id))
        counts["prescriptions"] += 1
    enc.status, enc.ended_at, enc.version = "finalized", now, enc.version + 1
    cs.status, cs.committed_at = "committed", now
    outbox.enqueue(db, "index_clinical_note", {"note_id": str(note.id)})
    audit.record(db, action="documentation.commit", category="modification", outcome="success",
                 patient_id=cs.patient_id, encounter_id=cs.encounter_id, resource_type="documentation_changeset",
                 resource_id=cs.id, details={**counts, "note_id": str(note.id), "sha256": expected_sha256[:16]}, **audit_ctx)
    if counts["prescriptions"]:
        audit.record(db, action="prescription.create", category="approval", outcome="success", patient_id=cs.patient_id,
                     encounter_id=cs.encounter_id, resource_type="prescription", details={"count": counts["prescriptions"],
                     "overridden_alerts": len((cs.safety_decision or {}).get("acknowledged", []))}, **audit_ctx)
    events.emit(db, "documentation.committed", run_id=run_id, encounter_id=cs.encounter_id, patient_id=cs.patient_id,
                agent_id="chart", node="chart_commit", status="succeeded", **counts)
    return {"note_id": str(note.id), "counts": counts, "approval_record_id": str(bundle_approval.id)}
