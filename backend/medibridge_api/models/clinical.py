"""Schema ``clinical``: patients, encounters and clinical facts.

Resource names follow HL7 FHIR R4 (Patient, Encounter, Condition, MedicationStatement,
MedicationRequest, AllergyIntolerance, Observation, Procedure, Consent, Composition).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, utcnow, uuid_pk, vocab_check

PATIENT_FK = "clinical.patients.id"
ENCOUNTER_FK = "clinical.encounters.id"
USER_FK = "auth.users.id"


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        vocab_check("sex_at_birth", "sex_at_birth"),
        Index("ix_patients_name_search", text("lower(family_name)"), text("lower(given_name)")),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Internal medical record number: the identifier clinicians see. Other identifiers are in patient_identifiers.
    mrn: Mapped[str] = mapped_column(String(20), unique=True)
    given_name: Mapped[str] = mapped_column(String(80))
    family_name: Mapped[str] = mapped_column(String(80))
    birth_date: Mapped[date] = mapped_column(Date)
    sex_at_birth: Mapped[str] = mapped_column(String(40))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    # Master patient index (EMPI): how the record was created; desk registrations name the registering user.
    registration_source: Mapped[str] = mapped_column(String(40), default="seed")
    registered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_reason: Mapped[str | None] = mapped_column(String(200))

    @property
    def display_name(self) -> str:
        return f"{self.given_name} {self.family_name}"


class PatientIdentifier(Base):
    """Administrative identifiers (national id, insurance...). Restricted: never in clinical views or handoffs."""

    __tablename__ = "patient_identifiers"
    __table_args__ = (
        vocab_check("system", "identifier_system"),
        UniqueConstraint("system", "value"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    system: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = created_at()


class PatientContact(Base):
    """Contact details. Restricted to administrators; excluded from every clinical serializer."""

    __tablename__ = "patient_contacts"
    __table_args__ = (vocab_check("kind", "contact_kind"), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = created_at()


class CareTeamAssignment(Base):
    """Patient scope: a clinician may read a patient's clinical record only with an active assignment."""

    __tablename__ = "care_team_assignments"
    __table_args__ = (
        vocab_check("role", "care_team_role"),
        Index("ix_care_team_active", "user_id", "patient_id", postgresql_where="revoked_at IS NULL"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    role: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(200))
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    granted_at: Mapped[datetime] = created_at()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Encounter(Base):
    __tablename__ = "encounters"
    __table_args__ = (
        vocab_check("status", "encounter_status"),
        vocab_check("encounter_class", "encounter_class"),
        Index("ix_encounters_patient_start", "patient_id", "started_at"),
        Index("ix_encounters_status", "status"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Guardrail 1: every encounter is bound to a verified patient.
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), nullable=False)
    encounter_class: Mapped[str] = mapped_column(String(40), default="ambulatory")
    status: Mapped[str] = mapped_column(String(40), default="planned")
    chief_complaint: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    attending_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    identity_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_method: Mapped[str | None] = mapped_column(String(80))  # e.g. "wristband+dob", "photo_id+dob"
    # Optimistic concurrency for status transitions.
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    patient: Mapped[Patient] = relationship(lazy="joined")


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        vocab_check("consent_type", "consent_type"),
        vocab_check("status", "consent_status"),
        vocab_check("method", "consent_method"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    consent_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(40))
    statement_version: Mapped[str] = mapped_column(String(40))  # version of the consent wording read to the patient
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    recorded_at: Mapped[datetime] = created_at()


# ---------------------------------------------------------------------------
# Clinical facts. Each carries provenance (guardrails 4 and 10).
# ---------------------------------------------------------------------------

class ProvenanceMixin:
    source_kind: Mapped[str] = mapped_column(String(40))
    # Machine-resolvable pointer to the evidence, e.g. "transcript:<uuid>#seg-12",
    # "document:<uuid>@v2#chunk-3", "note:<uuid>@v1", "seed:<scenario>".
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    # The original wording, preserved verbatim (never the normalized term).
    source_phrase: Mapped[str] = mapped_column(Text, default="")
    certainty: Mapped[str] = mapped_column(String(40), default="stated")
    status: Mapped[str] = mapped_column(String(40), default="confirmed")
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  server_default=func.now())


def provenance_checks() -> tuple:
    return (vocab_check("source_kind", "source_kind"), vocab_check("certainty", "certainty"),
            vocab_check("status", "fact_status"))


class Symptom(ProvenanceMixin, Base):
    __tablename__ = "symptoms"
    __table_args__ = (*provenance_checks(), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(40))
    code_system: Mapped[str | None] = mapped_column(String(40))
    negated: Mapped[bool] = mapped_column(Boolean, default=False)  # "no fever" is recorded, not dropped
    onset_text: Mapped[str | None] = mapped_column(String(120))
    duration_text: Mapped[str | None] = mapped_column(String(120))
    severity_text: Mapped[str | None] = mapped_column(String(120))
    body_site: Mapped[str | None] = mapped_column(String(120))


class Condition(ProvenanceMixin, Base):
    __tablename__ = "conditions"
    __table_args__ = (
        *provenance_checks(),
        vocab_check("clinical_status", "condition_clinical"),
        vocab_check("verification_status", "condition_verification"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(40))
    code_system: Mapped[str | None] = mapped_column(String(40))
    clinical_status: Mapped[str] = mapped_column(String(40), default="active")
    verification_status: Mapped[str] = mapped_column(String(40), default="confirmed")
    onset_date: Mapped[date | None] = mapped_column(Date)
    abatement_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")


class Procedure(ProvenanceMixin, Base):
    __tablename__ = "procedures"
    __table_args__ = (*provenance_checks(), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(40))
    performed_date: Mapped[date | None] = mapped_column(Date)
    performed_text: Mapped[str | None] = mapped_column(String(120))  # "about ten years ago"


class Medication(Base):
    """Local medication catalogue, linked to the knowledge graph by RxNorm concept id when known."""

    __tablename__ = "medications"
    __table_args__ = (UniqueConstraint("name", "strength", "dose_form"), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    rxcui: Mapped[str | None] = mapped_column(String(20), index=True)
    ingredient_rxcuis: Mapped[list] = mapped_column(JSONB, default=list)
    strength: Mapped[str] = mapped_column(String(80), default="")
    dose_form: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = created_at()


class MedicationStatement(ProvenanceMixin, Base):
    """What the patient is / was taking, as reported or documented. Not an order."""

    __tablename__ = "medication_statements"
    __table_args__ = (
        *provenance_checks(),
        vocab_check("med_status", "medstatement_status"),
        Index("ix_medstatements_patient_status", "patient_id", "med_status"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK))
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    medication_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.medications.id"))
    medication_text: Mapped[str] = mapped_column(String(300))  # as reported; may be partial ("atorva-something")
    dose_text: Mapped[str | None] = mapped_column(String(120))
    route: Mapped[str | None] = mapped_column(String(60))
    frequency_text: Mapped[str | None] = mapped_column(String(120))
    med_status: Mapped[str] = mapped_column(String(40), default="unknown")
    effective_start: Mapped[date | None] = mapped_column(Date)
    effective_end: Mapped[date | None] = mapped_column(Date)
    # When a clinician last reconciled this entry; None means never reconciled.
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))


class Prescription(Base):
    """FHIR MedicationRequest. Only a doctor can create one, and it becomes active only with an approval record."""

    __tablename__ = "prescriptions"
    __table_args__ = (
        vocab_check("status", "prescription_status"),
        # Guardrails 6 and 7: an active/completed prescription must reference the doctor's approval.
        CheckConstraint("status IN ('draft','cancelled','entered_in_error') OR approval_record_id IS NOT NULL",
                        name="active_requires_approval"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    medication_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.medications.id"))
    medication_text: Mapped[str] = mapped_column(String(300))
    dose_text: Mapped[str] = mapped_column(String(120))
    route: Mapped[str] = mapped_column(String(60))
    frequency_text: Mapped[str] = mapped_column(String(120))
    duration_text: Mapped[str] = mapped_column(String(120), default="")
    quantity_text: Mapped[str] = mapped_column(String(120), default="")
    refills: Mapped[int] = mapped_column(Integer, default=0)
    instructions: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    # Always a human doctor; there is no AI author column on purpose.
    prescriber_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    approval_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.approval_records.id"))
    authored_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Allergy(ProvenanceMixin, Base):
    __tablename__ = "allergies"
    __table_args__ = (
        *provenance_checks(),
        vocab_check("category", "allergy_category"),
        vocab_check("criticality", "allergy_criticality"),
        vocab_check("clinical_status", "allergy_clinical"),
        vocab_check("verification_status", "allergy_verification"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK))
    substance_text: Mapped[str] = mapped_column(String(200))
    substance_rxcui: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(40), default="medication")
    reaction_text: Mapped[str | None] = mapped_column(String(300))
    reaction_severity: Mapped[str | None] = mapped_column(String(40))
    criticality: Mapped[str] = mapped_column(String(40), default="unable_to_assess")
    clinical_status: Mapped[str] = mapped_column(String(40), default="active")
    verification_status: Mapped[str] = mapped_column(String(40), default="unconfirmed")


class VitalSign(Base):
    """FHIR Observation (vital-signs). Recorded only by an authenticated clinician; never generated."""

    __tablename__ = "vital_signs"
    __table_args__ = (vocab_check("kind", "vital_kind"), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    value: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    unit: Mapped[str] = mapped_column(String(20))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK), nullable=False)
    recorded_at: Mapped[datetime] = created_at()
    status: Mapped[str] = mapped_column(String(40), default="confirmed")


class ClinicalNote(Base):
    __tablename__ = "clinical_notes"
    __table_args__ = (
        vocab_check("note_type", "note_type"),
        vocab_check("status", "note_status"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    note_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    approval_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.approval_records.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ClinicalNoteVersion(Base):
    """Append-only note text history (the app role has INSERT/SELECT only)."""

    __tablename__ = "clinical_note_versions"
    __table_args__ = (
        UniqueConstraint("note_id", "version"),
        vocab_check("author_kind", "author_kind"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.clinical_notes.id"))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    author_kind: Mapped[str] = mapped_column(String(40))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    change_reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = created_at()


class TranscriptionRecord(Base):
    """Speech-to-text result. ``raw_segments`` is the untouched model output and is never modified."""

    __tablename__ = "transcription_records"
    __table_args__ = (vocab_check("status", "transcription_status"), {"schema": "clinical"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    consent_record_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.consent_records.id"))
    audio_storage_key: Mapped[str | None] = mapped_column(String(200))
    audio_sha256: Mapped[str | None] = mapped_column(String(64))
    audio_duration_s: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    model: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    # [{id, start, end, speaker, text, avg_logprob, no_speech_prob, words:[{w, start, end, p}], uncertain}]
    raw_segments: Mapped[list] = mapped_column(JSONB, default=list)
    # Same shape, after operator review; each corrected segment keeps {original_text, corrected_by}.
    reviewed_segments: Mapped[list | None] = mapped_column(JSONB)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    created_at: Mapped[datetime] = created_at()


class Observation(Base):
    """FHIR Observation: laboratory results and measured values (LOINC-coded where the source codes them).

    Imported results keep the source's code, value and unit exactly; nothing is derived or re-scaled.
    """

    __tablename__ = "observations"
    __table_args__ = (
        vocab_check("category", "observation_category"),
        Index("ix_observations_patient_code", "patient_id", "code", "effective_at"),
        {"schema": "clinical"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(PATIENT_FK), nullable=False)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(ENCOUNTER_FK), index=True)
    category: Mapped[str] = mapped_column(String(40), default="laboratory")
    code: Mapped[str | None] = mapped_column(String(40))
    code_system: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_text: Mapped[str | None] = mapped_column(String(300))
    unit: Mapped[str | None] = mapped_column(String(40))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_kind: Mapped[str] = mapped_column(String(40), default="record_retrieved")
    source_ref: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(40), default="final")
    created_at: Mapped[datetime] = created_at()

