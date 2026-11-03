"""Database-level guardrails: audit hash chain, append-only tables, least privilege, doctor-only actions."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from medibridge_api.models import (ApprovalRecord, ClinicalNote, DocumentVersion, Encounter, HandoffReport,
                                   HandoffVersion, MedicalDocument, Prescription)
from medibridge_api.services import audit


def _audit(db, n=3):
    for i in range(n):
        audit.record(db, action="test.event", category="workflow", outcome="success", details={"i": i})
    db.commit()


def test_audit_chain_is_intact_after_inserts(db):
    _audit(db)
    result = audit.verify_chain(db)
    assert result["intact"], result
    assert len(result["head_hash"]) == 64


def test_audit_details_reject_sensitive_keys(db):
    with pytest.raises(ValueError):
        audit.record(db, action="x", category="workflow", outcome="success", details={"password": "x"})


def test_app_role_cannot_update_or_delete_audit_rows(db):
    _audit(db, 1)
    for stmt in ("UPDATE audit.audit_logs SET action = 'forged'", "DELETE FROM audit.audit_logs"):
        with pytest.raises(ProgrammingError):  # permission denied for the app role
            db.execute(text(stmt))
        db.rollback()


def test_owner_cannot_update_delete_or_truncate_audit_rows(owner_engine, db):
    _audit(db, 1)
    for stmt in ("UPDATE audit.audit_logs SET action = 'forged'", "DELETE FROM audit.audit_logs",
                 "TRUNCATE audit.audit_logs"):
        with owner_engine.connect() as conn, pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(stmt))


def test_app_cannot_supply_its_own_hash(db):
    db.execute(text("INSERT INTO audit.audit_logs (id, action, category, outcome, prev_hash, row_hash, seq) "
                    "VALUES (gen_random_uuid(), 'x', 'workflow', 'success', 'forged', 'forged', 1)"))
    db.commit()
    assert audit.verify_chain(db)["intact"]
    assert db.execute(text("SELECT count(*) FROM audit.audit_logs WHERE row_hash = 'forged'")).scalar() == 0


def test_tampering_is_detected(owner_engine, db):
    _audit(db, 2)
    with owner_engine.begin() as conn:
        target = conn.execute(text("SELECT seq, action FROM audit.audit_logs ORDER BY seq DESC LIMIT 1")).one()
        conn.execute(text("ALTER TABLE audit.audit_logs DISABLE TRIGGER audit_logs_append_only"))
        conn.execute(text("UPDATE audit.audit_logs SET action = 'tampered' WHERE seq = :s"), {"s": target.seq})
    try:
        result = audit.verify_chain(db)
        assert not result["intact"]
        assert result["problems"][0] == {"seq": target.seq, "problem": "row_hash_mismatch"}
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE audit.audit_logs SET action = :a WHERE seq = :s"),
                         {"a": target.action, "s": target.seq})
            conn.execute(text("ALTER TABLE audit.audit_logs ENABLE TRIGGER audit_logs_append_only"))
    assert audit.verify_chain(db)["intact"]


def test_app_role_cannot_modify_roles_or_permissions(db):
    with pytest.raises(ProgrammingError):
        db.execute(text("INSERT INTO auth.roles (key, label, description) VALUES ('superuser', 'x', '')"))
    db.rollback()
    with pytest.raises(ProgrammingError):
        db.execute(text("UPDATE auth.permissions SET patient_scoped = false"))
    db.rollback()


def _encounter(db, patient, user):
    enc = Encounter(patient_id=patient.id, status="in_progress", created_by=user.id)
    db.add(enc)
    db.commit()
    return enc


def _approval(db, enc, user, role="doctor"):
    rec = ApprovalRecord(patient_id=enc.patient_id, encounter_id=enc.id, subject_type="prescription",
                         subject_id=uuid.uuid4(), subject_sha256="0" * 64, decision="approved", decided_by=user.id,
                         decided_role=role)
    db.add(rec)
    db.commit()
    return rec


def _rx(enc, prescriber, **kw):
    return Prescription(patient_id=enc.patient_id, encounter_id=enc.id, medication_text="amoxicillin 500 mg capsule",
                        dose_text="500 mg", route="oral", frequency_text="three times daily",
                        prescriber_user_id=prescriber.id, **kw)


def test_nurse_cannot_author_prescription(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    enc = _encounter(db, patient_factory(), nurse)
    db.add(_rx(enc, nurse))
    with pytest.raises(DBAPIError, match="active doctor"):
        db.commit()
    db.rollback()


def test_active_prescription_requires_doctor_approval(db, user_factory, patient_factory):
    doctor = user_factory("doctor")
    enc = _encounter(db, patient_factory(), doctor)
    db.add(_rx(enc, doctor, status="active"))
    with pytest.raises(IntegrityError, match="active_requires_approval"):
        db.commit()
    db.rollback()
    approval = _approval(db, enc, doctor)
    db.add(_rx(enc, doctor, status="active", approval_record_id=approval.id))
    db.commit()


def test_approval_for_another_patient_is_rejected(db, user_factory, patient_factory):
    doctor = user_factory("doctor")
    enc_a = _encounter(db, patient_factory(), doctor)
    enc_b = _encounter(db, patient_factory(), doctor)
    approval_b = _approval(db, enc_b, doctor)
    db.add(_rx(enc_a, doctor, status="active", approval_record_id=approval_b.id))
    with pytest.raises(DBAPIError, match="another patient"):
        db.commit()
    db.rollback()


def test_nurse_cannot_record_clinical_approval(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    enc = _encounter(db, patient_factory(), nurse)
    with pytest.raises(DBAPIError, match="require an active doctor"):
        _approval(db, enc, nurse, role="doctor")  # lying about the role does not help
    db.rollback()


def test_finalized_note_cannot_return_to_draft(db, user_factory, patient_factory):
    doctor = user_factory("doctor")
    enc = _encounter(db, patient_factory(), doctor)
    note = ClinicalNote(patient_id=enc.patient_id, encounter_id=enc.id, note_type="progress_note", status="final",
                        author_user_id=doctor.id)
    db.add(note)
    db.commit()
    note.status = "draft"
    with pytest.raises(DBAPIError, match="cannot move to status"):
        db.commit()
    db.rollback()


def test_encounter_cannot_move_to_another_patient(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    enc = _encounter(db, patient_factory(), nurse)
    enc.patient_id = patient_factory().id
    with pytest.raises(DBAPIError, match="another patient"):
        db.commit()
    db.rollback()


def test_handoff_versions_are_append_only(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    enc = _encounter(db, patient_factory(), nurse)
    report = HandoffReport(patient_id=enc.patient_id, encounter_id=enc.id, status="draft")
    db.add(report)
    db.flush()
    db.add(HandoffVersion(handoff_id=report.id, version=1, content={}, evidence_bundle={}, content_sha256="0" * 64,
                          author_kind="ai"))
    db.commit()
    with pytest.raises(ProgrammingError):
        db.execute(text("UPDATE workflow.handoff_versions SET content_sha256 = 'x'"))
    db.rollback()


def test_document_version_content_is_immutable(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    patient = patient_factory()
    doc = MedicalDocument(patient_id=patient.id, document_type="lab_report", title="Synthetic CBC")
    db.add(doc)
    db.flush()
    version = DocumentVersion(document_id=doc.id, version=1, storage_key="aa/aa.pdf", content_sha256="a" * 64,
                              mime_type="application/pdf", size_bytes=10, created_by=nurse.id)
    db.add(version)
    db.commit()
    version.ingestion_status = "indexed"  # allowed: status fields
    db.commit()
    version.content_sha256 = "b" * 64
    with pytest.raises(DBAPIError, match="immutable"):
        db.commit()
    db.rollback()
