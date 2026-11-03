"""Document ingestion: storage -> text -> identifier scan -> chunks -> embeddings -> Qdrant.

Each step records its outcome on the document version (``ingestion_status`` / ``ingestion_error``) and emits a
workflow event, so the UI shows real progress. Failures are typed and visible ("failed: no_text_layer"); a
failed document is reported as *unavailable*, never as normal findings. Chunk ids are deterministic
(uuid5 of version id + index), so re-running ingestion is idempotent.
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_sessionmaker
from ..guardrails import phi
from ..models import DocumentChunk, DocumentVersion, MedicalDocument
from ..models.base import utcnow
from ..providers import get_llm
from ..providers.base import ProviderError
from . import doc_text, events, storage, vector_index

CHUNK_NS = uuid.UUID("6f1c7e0a-5a39-4b0e-9a55-2d7c3b8a9e11")


def chunk_id(version_id: uuid.UUID, index: int) -> uuid.UUID:
    return uuid.uuid5(CHUNK_NS, f"{version_id}:{index}")


def _set(db: Session, v: DocumentVersion, doc: MedicalDocument, status: str, error: str | None = None, **attrs) -> None:
    v.ingestion_status, v.ingestion_error = status, error
    events.emit(db, f"document.{status}", patient_id=doc.patient_id, encounter_id=doc.encounter_id,
                status=status, tool_name="ingestion", document_id=str(doc.id), document_version=v.version,
                **({"error_code": error} if error else {}), **attrs)
    db.commit()


def ingest_version(version_id: uuid.UUID) -> dict:
    started = time.perf_counter()
    with get_sessionmaker()() as db:
        v = db.get(DocumentVersion, version_id)
        if v is None:
            return {"status": "missing"}
        doc = db.get(MedicalDocument, v.document_id)
        _set(db, v, doc, "extracting")
        try:
            data = storage.read(v.storage_key, v.content_sha256)
        except (OSError, ValueError):
            _set(db, v, doc, "failed", "storage_integrity_failure")
            return {"status": "failed", "error": "storage_integrity_failure"}
        if v.mime_type == "application/pdf":
            try:
                ex = doc_text.extract_pdf(data)
            except doc_text.ExtractionError as exc:
                _set(db, v, doc, "failed", exc.code[:120])
                return {"status": "failed", "error": exc.code}
        else:
            ex = doc_text.Extracted(text=data.decode("utf-8", errors="replace"), page_count=1, page_starts=[0])
        scan = phi.scan(ex.text)
        if v.extracted_text is None:
            v.extracted_text = ex.text
        v.page_count, v.phi_scan = ex.page_count, scan.findings
        chunks = doc_text.chunk(ex)
        existing = {c.chunk_index for c in db.scalars(select(DocumentChunk).where(DocumentChunk.document_version_id == v.id))}
        for c in chunks:
            if c.index not in existing:
                db.add(DocumentChunk(id=chunk_id(v.id, c.index), document_version_id=v.id, patient_id=doc.patient_id,
                                     chunk_index=c.index, section=c.section, page_from=c.page_from, page_to=c.page_to,
                                     char_start=c.char_start, char_end=c.char_end, text=c.text, text_sha256=c.sha256))
        _set(db, v, doc, "extracted", chunks=len(chunks), identifier_kinds=len(scan.findings))
        if not chunks:
            _set(db, v, doc, "failed", "no_chunks")
            return {"status": "failed", "error": "no_chunks"}

        _set(db, v, doc, "indexing")
        # Embed the redacted text: identifiers are not needed for retrieval.
        redacted = [phi.scan(f"{doc.title}. {c.section}. {c.text}").text for c in chunks]
        try:
            vectors, usage = get_llm().get_embeddings(redacted)
            points = [vector_index.ChunkPoint(chunk_id(v.id, c.index), doc.patient_id, doc.encounter_id, doc.id,
                                              v.version, doc.document_type, doc.clinical_date,
                                              f"document:{doc.id}@v{v.version}#chunk-{c.index}", text)
                      for c, text in zip(chunks, redacted)]
            vector_index.upsert(points, vectors, usage.model)
        except ProviderError as exc:
            _set(db, v, doc, "failed", f"embedding_unavailable:{exc.code}")
            return {"status": "failed", "error": "embedding_unavailable"}
        except Exception as exc:  # noqa: BLE001 - Qdrant connectivity / schema errors
            _set(db, v, doc, "failed", f"index_unavailable:{type(exc).__name__}"[:120])
            return {"status": "failed", "error": "index_unavailable"}
        v.embedding_model, v.index_collection, v.indexed_at = usage.model, vector_index.collection_name(), utcnow()
        elapsed = int((time.perf_counter() - started) * 1000)
        _set(db, v, doc, "indexed", chunks=len(chunks), duration_ms=elapsed, embed_ms=usage.total_ms)
        return {"status": "indexed", "chunks": len(chunks), "ms": elapsed}


def pending_versions(db: Session, include_failed: bool = False) -> list[uuid.UUID]:
    statuses = ["pending", "extracting", "extracted", "indexing"] + (["failed"] if include_failed else [])
    return list(db.scalars(select(DocumentVersion.id).where(DocumentVersion.ingestion_status.in_(statuses))))
