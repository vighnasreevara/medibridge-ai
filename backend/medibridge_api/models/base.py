"""Declarative base and shared column helpers.

Conventions
- UUID primary keys generated in Python (no sequential ids exposed in URLs).
- Every timestamp is timezone-aware UTC.
- Enumerations are TEXT + CHECK constraints (the ``VOCAB`` dict), not Postgres ENUM types,
  so vocabulary changes are plain migrations.
- Clinical rows are never hard-deleted: ``deleted_at``/``deleted_reason`` or a status of
  ``entered_in_error`` (FHIR) is used instead.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SCHEMAS = ("auth", "clinical", "docs", "workflow", "audit")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(UTC)


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow,
                         server_default=func.now())


def vocab_check(column: str, vocab: str) -> CheckConstraint:
    values = ", ".join(f"'{v}'" for v in VOCAB[vocab])
    return CheckConstraint(f"{column} IN ({values})", name=f"{column}_vocab")


# Controlled vocabularies, aligned with HL7 FHIR R4 value sets where one exists.
VOCAB: dict[str, tuple[str, ...]] = {
    "user_status": ("active", "disabled"),
    "sex_at_birth": ("female", "male", "intersex", "unknown"),
    "identifier_system": ("mrn", "national_id", "insurance", "passport", "external_ehr"),
    "contact_kind": ("phone", "email", "address"),
    # Where a clinical fact came from (guardrail 10: provenance is a first-class field).
    "source_kind": ("patient_reported", "clinician_documented", "record_retrieved", "device", "unconfirmed",
                    "conflicting"),
    "certainty": ("stated", "uncertain", "inferred"),
    # FHIR Encounter.status (subset) + MediBridge review states
    "encounter_status": ("planned", "in_progress", "awaiting_review", "finalized", "cancelled",
                         "entered_in_error"),
    "encounter_class": ("ambulatory", "emergency", "inpatient", "virtual"),
    "care_team_role": ("attending", "consulting", "nursing", "intake"),
    "fact_status": ("draft", "confirmed", "refuted", "entered_in_error"),
    # FHIR Condition.clinicalStatus / verificationStatus
    "condition_clinical": ("active", "recurrence", "relapse", "inactive", "remission", "resolved", "unknown"),
    "condition_verification": ("unconfirmed", "provisional", "differential", "confirmed", "refuted",
                               "entered_in_error"),
    # FHIR MedicationStatement.status
    "medstatement_status": ("active", "completed", "stopped", "on_hold", "intended", "not_taken", "unknown",
                            "entered_in_error"),
    # FHIR MedicationRequest.status
    "prescription_status": ("draft", "active", "on_hold", "cancelled", "completed", "stopped",
                            "entered_in_error"),
    "allergy_category": ("medication", "food", "environment", "biologic"),
    "allergy_criticality": ("low", "high", "unable_to_assess"),
    "allergy_clinical": ("active", "inactive", "resolved"),
    "allergy_verification": ("unconfirmed", "presumed", "confirmed", "refuted", "entered_in_error"),
    "reaction_severity": ("mild", "moderate", "severe"),
    "vital_kind": ("heart_rate", "systolic_bp", "diastolic_bp", "respiratory_rate", "temperature", "spo2",
                   "weight", "height", "pain_score"),
    "note_type": ("progress_note", "intake_note", "assessment_plan", "discharge_summary", "consult_note"),
    "note_status": ("draft", "final", "amended", "entered_in_error"),
    "consent_type": ("audio_recording", "ai_processing"),
    "consent_status": ("granted", "declined", "withdrawn"),
    "consent_method": ("verbal_witnessed", "written", "electronic"),
    "document_type": ("lab_report", "imaging_report", "discharge_summary", "referral_letter", "clinical_note",
                      "pathology_report", "medication_list", "ecg_report", "procedure_report", "other"),
    "document_status": ("active", "superseded", "entered_in_error"),
    "ingestion_status": ("pending", "extracting", "extracted", "indexing", "indexed", "failed",
                         "rejected"),
    "transcription_status": ("pending", "transcribing", "needs_review", "reviewed", "failed", "manual"),
    "handoff_status": ("generating", "draft", "in_review", "changes_requested", "approved", "rejected",
                       "superseded", "failed"),
    "author_kind": ("ai", "human", "mixed"),
    "approval_subject": ("handoff_version", "clinical_note_version", "prescription", "documentation_bundle",
                         "safety_override", "after_visit_summary"),
    "approval_decision": ("approved", "rejected", "changes_requested"),
    "run_status": ("queued", "running", "waiting_human", "completed", "failed", "cancelled", "interrupted",
                   "blocked"),
    # FHIR Observation.category (subset)
    "observation_category": ("laboratory", "vital-signs", "procedure", "survey"),
    "registration_source": ("synthea", "registration_desk", "seed"),
    "avs_status": ("draft", "released", "superseded"),
    "execution_kind": ("agent", "tool", "llm", "retrieval", "guardrail", "gate", "a2a"),
    "execution_status": ("running", "succeeded", "failed", "skipped", "timed_out", "blocked"),
    "outbox_status": ("pending", "processing", "done", "failed"),
    "audit_category": ("authentication", "access", "approval", "modification", "safety", "policy", "security",
                       "administration", "workflow"),
    "audit_outcome": ("success", "failure", "allowed", "denied"),
}
