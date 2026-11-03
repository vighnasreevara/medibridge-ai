"""Schema ``docs``: medical documents, immutable versions and retrieval chunks.

Original files live in the protected local document store, addressed by content SHA-256
(never by patient name). Chunk text stays in Postgres; Qdrant only holds vectors plus the
minimal metadata needed to filter, so displayed text is always re-read here after authorization.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at, updated_at, uuid_pk, vocab_check


class MedicalDocument(Base):
    __tablename__ = "medical_documents"
    __table_args__ = (
        vocab_check("document_type", "document_type"),
        vocab_check("status", "document_status"),
        Index("ix_documents_patient_date", "patient_id", "clinical_date"),
        {"schema": "docs"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"), nullable=False)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical.encounters.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    clinical_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(String(200), default="")  # issuing department / facility
    status: Mapped[str] = mapped_column(String(40), default="active")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    # Owner: the user (or "system:seed") that brought the document into the record.
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_reason: Mapped[str | None] = mapped_column(String(200))

    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document", order_by="DocumentVersion.version")


class DocumentVersion(Base):
    """Content columns are immutable (DB trigger); only ingestion status/index fields may change."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version"),
        vocab_check("ingestion_status", "ingestion_status"),
        {"schema": "docs"},
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("docs.medical_documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(200))  # "<sha256[:2]>/<sha256>.<ext>"
    content_sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    # Identifier findings from the PHI scanner run at ingestion: [{kind, count}] — never the values.
    phi_scan: Mapped[list] = mapped_column(JSONB, default=list)
    ingestion_status: Mapped[str] = mapped_column(String(40), default="pending")
    ingestion_error: Mapped[str | None] = mapped_column(String(300))
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    index_collection: Mapped[str | None] = mapped_column(String(120))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_reason: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth.users.id"))
    created_at: Mapped[datetime] = created_at()

    document: Mapped[MedicalDocument] = relationship(back_populates="versions")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_version_id", "chunk_index"), {"schema": "docs"})

    id: Mapped[uuid.UUID] = uuid_pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("docs.document_versions.id"))
    # Denormalized for scope checks without joins; must equal the parent document's patient.
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(120), default="")
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
