"""Handoff persistence: append-only versions bound to a content hash; approvals bind to that exact hash."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from medibridge_api.models import ApprovalRecord, Encounter, HandoffReport, HandoffVersion
from medibridge_api.models.base import utcnow
from medibridge_api.services import audit


class HandoffConflict(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def canonical_sha(content: dict, evidence_bundle: dict) -> str:
    payload = json.dumps({"content": content, "evidence": evidence_bundle}, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_report(db: Session, encounter_id: uuid.UUID) -> HandoffReport | None:
    return db.scalars(select(HandoffReport).where(HandoffReport.encounter_id == encounter_id)
                      .order_by(HandoffReport.created_at.desc())).first()


def current_version(db: Session, report: HandoffReport) -> HandoffVersion | None:
    return db.scalars(select(HandoffVersion).where(HandoffVersion.handoff_id == report.id,
                                                   HandoffVersion.version == report.current_version)).first()


def create_version(db: Session, *, encounter_id: uuid.UUID, patient_id: uuid.UUID, run_id: uuid.UUID | None,
                   content: dict, evidence_bundle: dict, author_kind: str, author_user_id: uuid.UUID | None,
                   model: str | None, prompt_version: str | None, validation: dict, change_summary: str,
                   status: str = "in_review") -> HandoffVersion:
    report = get_report(db, encounter_id)
    if report is None:
        report = HandoffReport(id=uuid.uuid4(), patient_id=patient_id, encounter_id=encounter_id, run_id=run_id,
                               status="generating", current_version=0)
        db.add(report)
        db.flush()
    if report.status == "approved":
        raise HandoffConflict("already_approved", "An approved handoff cannot be changed; documentation follows it.")
    version = HandoffVersion(id=uuid.uuid4(), handoff_id=report.id, version=report.current_version + 1,
                             content=content, evidence_bundle=evidence_bundle,
                             content_sha256=canonical_sha(content, evidence_bundle), author_kind=author_kind,
                             author_user_id=author_user_id, model=model, prompt_version=prompt_version,
                             validation=validation, change_summary=change_summary[:500])
    db.add(version)
    report.current_version = version.version
    report.status = status
    if run_id:
        report.run_id = run_id
    enc = db.get(Encounter, encounter_id)
    if status == "in_review" and enc.status in ("planned", "in_progress"):
        enc.status = "awaiting_review"
    return version


def decide(db: Session, *, encounter_id: uuid.UUID, version: int, expected_sha256: str, decision: str,
           rationale: str, doctor_id: uuid.UUID, audit_ctx: dict) -> ApprovalRecord:
    """Record a doctor's decision on exactly the version (and hash) they reviewed."""
    report = get_report(db, encounter_id)
    if report is None:
        raise HandoffConflict("no_handoff", "There is no handoff for this encounter.")
    v = current_version(db, report)
    if v is None or v.version != version:
        raise HandoffConflict("stale_version", "A newer handoff version exists; review it before deciding.")
    if v.content_sha256 != expected_sha256:
        raise HandoffConflict("hash_mismatch", "The handoff changed since it was opened; reload and review again.")
    if report.status not in ("in_review", "changes_requested", "draft"):
        raise HandoffConflict("not_reviewable", f"Handoff is {report.status}.")
    record = ApprovalRecord(id=uuid.uuid4(), patient_id=report.patient_id, encounter_id=encounter_id,
                            subject_type="handoff_version", subject_id=v.id, subject_sha256=v.content_sha256,
                            decision=decision, rationale=rationale, decided_by=doctor_id, decided_role="doctor")
    db.add(record)
    report.status = {"approved": "approved", "rejected": "rejected", "changes_requested": "changes_requested"}[decision]
    if decision == "approved":
        report.approved_version, report.approved_by, report.approved_at = v.version, doctor_id, utcnow()
    audit.record(db, action=f"handoff.{decision}", category="approval", outcome="success",
                 patient_id=report.patient_id, encounter_id=encounter_id, resource_type="handoff_version",
                 resource_id=v.id, details={"version": v.version, "sha256": v.content_sha256[:16]}, **audit_ctx)
    return record
