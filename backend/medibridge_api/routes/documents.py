"""Medical documents (patient-scoped): metadata, redacted text, audited original, search, upload, reindex."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..guardrails import phi
from ..models import DocumentChunk, DocumentVersion, MedicalDocument, User
from ..security.deps import Principal, ensure_patient_scope, require
from ..services import audit, clinical_views as views, outbox, retrieval, storage

router = APIRouter(prefix="/api/documents", tags=["documents"])
MAX_UPLOAD = 25 * 1024 * 1024
DOC_TYPES = Literal["lab_report", "imaging_report", "discharge_summary", "referral_letter", "clinical_note",
                    "pathology_report", "medication_list", "other"]


def _scoped(db: Session, principal: Principal, document_id: uuid.UUID, permission: str) -> MedicalDocument:
    doc = db.get(MedicalDocument, document_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(404, detail={"code": "not_found"})
    ensure_patient_scope(db, principal, doc.patient_id, permission)
    return doc


def _version(db: Session, doc: MedicalDocument, version: int | None) -> DocumentVersion:
    v = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id,
                                                 DocumentVersion.version == (version or doc.current_version))).first()
    if v is None:
        raise HTTPException(404, detail={"code": "version_not_found"})
    return v


@router.get("/search")
def search(patient_id: uuid.UUID, q: str = Query(..., min_length=2, max_length=200), limit: int = Query(6, ge=1, le=20),
           principal: Principal = Depends(require("document.read")), db: Session = Depends(get_db)) -> dict:
    ensure_patient_scope(db, principal, patient_id, "document.read")
    try:
        res = retrieval.retrieve(db, patient_id=patient_id, query=q, limit=limit)
    except retrieval.RetrievalUnavailable as exc:
        raise HTTPException(503, detail={"code": "retrieval_unavailable", "reason": exc.code,
                                         "message": "Document search is unavailable right now."})
    audit.record(db, action="document.search", category="access", outcome="allowed", patient_id=patient_id,
                 resource_type="document_search", details={"results": len(res.evidence)}, **principal.audit_ctx())
    db.commit()
    return {"evidence": [e.as_dict() for e in res.evidence], "coverage": res.coverage, "timings_ms": res.timings_ms}


@router.get("/{document_id}")
def detail(document_id: uuid.UUID, principal: Principal = Depends(require("document.read")),
           db: Session = Depends(get_db)) -> dict:
    doc = _scoped(db, principal, document_id, "document.read")
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                          .order_by(DocumentVersion.version)).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    audit.record(db, action="document.read", category="access", outcome="allowed", patient_id=doc.patient_id,
                 resource_type="document", resource_id=doc.id, details={"view": "metadata"}, **principal.audit_ctx())
    db.commit()
    current = next((v for v in versions if v.version == doc.current_version), None)
    return {**views.document_view(doc, current), "patient_id": str(doc.patient_id),
            "versions": [{"version": v.version, "content_sha256": v.content_sha256, "size_bytes": v.size_bytes,
                          "mime_type": v.mime_type, "ingestion_status": v.ingestion_status,
                          "ingestion_error": v.ingestion_error, "created_at": v.created_at,
                          "created_by": names.get(v.created_by), "change_reason": v.change_reason,
                          "embedding_model": v.embedding_model, "indexed_at": v.indexed_at} for v in versions]}


@router.get("/{document_id}/text")
def text(document_id: uuid.UUID, version: int | None = None, principal: Principal = Depends(require("document.read")),
         db: Session = Depends(get_db)) -> dict:
    """Identifier-redacted text with chunk boundaries for citation highlighting (default document view)."""
    doc = _scoped(db, principal, document_id, "document.read")
    v = _version(db, doc, version)
    chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.document_version_id == v.id)
                        .order_by(DocumentChunk.chunk_index)).all()
    audit.record(db, action="document.read", category="access", outcome="allowed", patient_id=doc.patient_id,
                 resource_type="document", resource_id=doc.id, details={"view": "redacted_text", "version": v.version},
                 **principal.audit_ctx())
    db.commit()
    return {"document_id": str(doc.id), "version": v.version, "ingestion_status": v.ingestion_status,
            "ingestion_error": v.ingestion_error, "phi_scan": v.phi_scan, "redacted": True,
            "chunks": [{"index": c.chunk_index, "section": c.section, "page_from": c.page_from, "page_to": c.page_to,
                        "text": phi.scan(c.text).text, "evidence_id": f"document:{doc.id}@v{v.version}#chunk-{c.chunk_index}"}
                       for c in chunks]}


@router.get("/{document_id}/original")
def original(document_id: uuid.UUID, reason: str = Query(..., min_length=5, max_length=200), version: int | None = None,
             principal: Principal = Depends(require("document.read")), db: Session = Depends(get_db)) -> Response:
    """The unredacted original file. Requires a stated reason; every access is audited."""
    doc = _scoped(db, principal, document_id, "document.read")
    v = _version(db, doc, version)
    try:
        data = storage.read(v.storage_key, v.content_sha256)
    except (OSError, ValueError):
        raise HTTPException(500, detail={"code": "storage_integrity_failure"})
    audit.record(db, action="document.original.read", category="access", outcome="allowed", patient_id=doc.patient_id,
                 resource_type="document", resource_id=doc.id,
                 details={"version": v.version, "reason_given": True, "reason_length": len(reason),
                          "identifier_kinds": [f["kind"] for f in v.phi_scan]}, **principal.audit_ctx())
    db.commit()
    return Response(data, media_type=v.mime_type, headers={
        "Content-Disposition": f'inline; filename="document-{doc.id}-v{v.version}.pdf"',
        # The viewer embeds this response in an iframe on the same origin.
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; object-src 'self'; style-src 'unsafe-inline'"})


@router.post("", status_code=201)
async def upload(patient_id: uuid.UUID = Form(...), document_type: DOC_TYPES = Form(...),
                 title: str = Form(..., min_length=2, max_length=300), clinical_date: date | None = Form(None),
                 source_name: str = Form("", max_length=200), encounter_id: uuid.UUID | None = Form(None),
                 file: UploadFile = File(...), principal: Principal = Depends(require("document.upload")),
                 db: Session = Depends(get_db)) -> dict:
    ensure_patient_scope(db, principal, patient_id, "document.upload")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, detail={"code": "too_large"})
    is_pdf = data.startswith(b"%PDF")
    if not is_pdf and (file.content_type or "") != "text/plain":
        raise HTTPException(415, detail={"code": "unsupported_type", "message": "Upload a PDF or plain-text report."})
    key, digest = storage.put(data, "pdf" if is_pdf else "txt")
    doc = MedicalDocument(id=uuid.uuid4(), patient_id=patient_id, encounter_id=encounter_id, document_type=document_type,
                          title=title.strip(), clinical_date=clinical_date, source_name=source_name.strip(),
                          created_by=principal.user_id)
    db.add(doc)
    db.flush()
    v = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, version=1, storage_key=key, content_sha256=digest,
                        mime_type="application/pdf" if is_pdf else "text/plain", size_bytes=len(data),
                        created_by=principal.user_id)
    db.add(v)
    outbox.enqueue(db, "ingest_document_version", {"version_id": str(v.id)})
    audit.record(db, action="document.upload", category="modification", outcome="success", patient_id=patient_id,
                 encounter_id=encounter_id, resource_type="document", resource_id=doc.id,
                 details={"document_type": document_type, "size_bytes": len(data)}, **principal.audit_ctx())
    db.commit()
    return views.document_view(doc, v)


@router.post("/{document_id}/reindex", status_code=202)
def reindex(document_id: uuid.UUID, principal: Principal = Depends(require("document.upload")),
            db: Session = Depends(get_db)) -> dict:
    doc = _scoped(db, principal, document_id, "document.upload")
    v = _version(db, doc, None)
    outbox.enqueue(db, "ingest_document_version", {"version_id": str(v.id)})
    audit.record(db, action="document.reindex", category="modification", outcome="success", patient_id=doc.patient_id,
                 resource_type="document", resource_id=doc.id, **principal.audit_ctx())
    db.commit()
    return {"queued": True}
