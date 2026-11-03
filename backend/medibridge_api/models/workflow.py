"""Schema ``workflow``: handoffs, approvals, agent runs, execution telemetry and the outbox.

Telemetry tables (``agent_executions``, ``workflow_events``) hold identifiers, timings, statuses
and counts only, never transcript text, document text, embeddings or patient demographics.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, updated_at, uuid_pk, vocab_check

USER_FK = "auth.users.id"


class HandoffReport(Base):
    __tablename__ = "handoff_reports"
    __table_args__ = (vocab_check("status", "handoff_status"), {"schema": "workflow"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.encounters.id"), index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.workflow_runs.id"))
    status: Mapped[str] = mapped_column(String(40), default="generating")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    approved_version: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class HandoffVersion(Base):
    """Append-only. ``content_sha256`` is what a doctor's approval binds to."""

    __tablename__ = "handoff_versions"
    __table_args__ = (
        UniqueConstraint("handoff_id", "version"),
        vocab_check("author_kind", "author_kind"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    handoff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow.handoff_reports.id"))
    version: Mapped[int] = mapped_column(Integer)
    # Structured sections; every historical claim carries evidence_ids into evidence_bundle.
    content: Mapped[dict] = mapped_column(JSONB)
    evidence_bundle: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    author_kind: Mapped[str] = mapped_column(String(40))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)  # guardrail results for this version
    change_summary: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = created_at()


class ApprovalRecord(Base):
    """A clinician's decision, bound to the exact content hash they reviewed. Append-only."""

    __tablename__ = "approval_records"
    __table_args__ = (
        vocab_check("subject_type", "approval_subject"),
        vocab_check("decision", "approval_decision"),
        Index("ix_approvals_subject", "subject_type", "subject_id"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.encounters.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID]
    subject_sha256: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(40))
    rationale: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    decided_role: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[datetime] = created_at()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        vocab_check("status", "run_status"),
        Index("ix_runs_encounter", "encounter_id", "created_at"),
        Index("ix_runs_status", "status"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.encounters.id"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"))
    kind: Mapped[str] = mapped_column(String(40), default="intake")
    status: Mapped[str] = mapped_column(String(40), default="queued")
    current_node: Mapped[str | None] = mapped_column(String(60))
    waiting_gate: Mapped[str | None] = mapped_column(String(60))
    # Duplicate-submission guard: a client-generated key, unique per run.
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    langgraph_thread_id: Mapped[str] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(String(300))  # no PHI
    started_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    created_at: Mapped[datetime] = created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at()


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (
        vocab_check("kind", "execution_kind"),
        vocab_check("status", "execution_status"),
        Index("ix_exec_run", "run_id", "started_at"),
        Index("ix_exec_agent_time", "agent_id", "started_at"),
        {"schema": "workflow"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow.workflow_runs.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.agent_executions.id"))
    kind: Mapped[str] = mapped_column(String(40))
    agent_id: Mapped[str] = mapped_column(String(60))
    node: Mapped[str] = mapped_column(String(60))
    tool_name: Mapped[str | None] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="running")
    started_at: Mapped[datetime] = created_at()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(80))
    model_digest: Mapped[str | None] = mapped_column(String(80))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    load_ms: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    # Counts / identifiers only, e.g. {"evidence_items": 7, "retrieval_hits": 4, "guardrails": {"G4": "pass"}}
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(80))  # optional LangSmith correlation


class WorkflowEvent(Base):
    """Ordered event log. The SSE stream replays from ``seq``; the UI derives progress only from these rows."""

    __tablename__ = "workflow_events"
    __table_args__ = (Index("ix_events_run", "run_id", "seq"), {"schema": "workflow"})

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4, unique=True)
    type: Mapped[str] = mapped_column(String(60))  # run.started, node.started, node.completed, gate.waiting, ...
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.workflow_runs.id"))
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.encounters.id"))
    # Patient scope for SSE fan-out filtering; never sent to clients that lack scope.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.patients.id"))
    agent_id: Mapped[str | None] = mapped_column(String(60))
    node: Mapped[str | None] = mapped_column(String(60))
    tool_name: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str | None] = mapped_column(String(40))
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)  # no PHI
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    created_at: Mapped[datetime] = created_at()


class OutboxItem(Base):
    """Side effects that must follow a committed transaction (e.g. index an approved note in Qdrant)."""

    __tablename__ = "outbox"
    __table_args__ = (vocab_check("status", "outbox_status"), Index("ix_outbox_pending", "status", "created_at"),
                      {"schema": "workflow"})

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentationChangeSet(Base):
    """A prepared documentation change set (note, facts, prescriptions) awaiting the doctor's commit decision.

    The commit step re-validates ``content_sha256``; anything changed after preparation must be prepared again.
    """

    __tablename__ = "documentation_changesets"
    __table_args__ = (Index("ix_changesets_encounter", "encounter_id", "created_at"), {"schema": "workflow"})

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.workflow_runs.id"))
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.encounters.id"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"))
    handoff_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow.handoff_versions.id"))
    content: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="prepared")  # prepared | committed | superseded
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    prepared_by: Mapped[uuid.UUID] = mapped_column(ForeignKey(USER_FK))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    # Medication Safety screen of the proposed orders (kept outside ``content`` so the hash the doctor binds to
    # does not change), and the doctor's decision on its alerts.
    safety_screen: Mapped[dict | None] = mapped_column(JSONB)
    safety_decision: Mapped[dict | None] = mapped_column(JSONB)


class AfterVisitSummary(Base):
    """Patient-facing after-visit summary (AVS): this visit only - reason, medicines and how to take them, next steps.

    Generated after the encounter is committed; a doctor releases it to the patient.
    """

    __tablename__ = "after_visit_summaries"
    __table_args__ = (vocab_check("status", "avs_status"), Index("ix_avs_encounter", "encounter_id", "created_at"),
                      {"schema": "workflow"})

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.encounters.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow.workflow_runs.id"))
    content: Mapped[dict] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    model: Mapped[str | None] = mapped_column(String(80))
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    released_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(USER_FK))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
