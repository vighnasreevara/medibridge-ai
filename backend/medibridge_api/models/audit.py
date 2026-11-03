"""Schema ``audit``: append-only, hash-chained audit log (guardrail 12).

``seq``, ``prev_hash`` and ``row_hash`` are assigned by a BEFORE INSERT trigger under an advisory lock,
so the chain order is the commit order and the application cannot supply its own hashes.
UPDATE, DELETE and TRUNCATE are rejected by triggers, and the app role only has INSERT/SELECT.
``details`` must not contain clinical content: identifiers, counts and reason codes only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, FetchedValue, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow, uuid_pk, vocab_check


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        vocab_check("category", "audit_category"),
        vocab_check("outcome", "audit_outcome"),
        Index("ix_audit_time", "occurred_at"),
        Index("ix_audit_patient", "patient_id", "occurred_at"),
        Index("ix_audit_actor", "actor_user_id", "occurred_at"),
        Index("ix_audit_category", "category", "occurred_at"),
        {"schema": "audit"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    seq: Mapped[int] = mapped_column(BigInteger, unique=True, server_default=FetchedValue())
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  server_default=func.now())
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # no FK: survives user removal
    actor_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80))  # e.g. auth.login, patient.read, handoff.approve
    category: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(40))
    patient_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), server_default=FetchedValue())
    row_hash: Mapped[str] = mapped_column(String(64), server_default=FetchedValue())
