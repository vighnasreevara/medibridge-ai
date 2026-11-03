"""Authorized hybrid document retrieval (RAG evidence) for one patient.

Pipeline: BM25 over the patient's current chunks (Postgres, medical tokenisation) and dense bge-m3 search in
Qdrant run side by side; their rankings are fused by reciprocal rank fusion (RRF). An optional local cross-encoder
(RERANKER_MODEL, off by default because it did not help on the cardiology benchmark) can re-rank the fused top
candidates; its ranking then joins the fusion as a third vote, so a weak re-ranker cannot override strong lexical
and semantic agreement. If the dense side is unavailable, retrieval continues lexically
and says so (``mode``). Every candidate is re-validated against Postgres before use:
- the chunk belongs to the requested patient (index metadata is never trusted alone);
- it comes from the document's current version and the document is not deleted/entered in error;
- the passage text is identifier-scanned and redacted.
The result also reports coverage (documents on file, indexed, failed), so callers can distinguish
"no relevant documents" from "documents exist but could not be searched".
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..guardrails import phi
from ..models import DocumentChunk, DocumentVersion, MedicalDocument
from ..providers import get_llm
from ..providers.base import ProviderError
from . import lexical, rerank, vector_index

CANDIDATES = 20  # per ranker
RERANK_TOP = 12


class RetrievalUnavailable(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Evidence:
    evidence_id: str
    document_id: str
    document_version: int
    title: str
    document_type: str
    clinical_date: date | None
    source_name: str
    section: str
    page: int | None
    text: str
    score: float
    source_ref: str
    ranks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["clinical_date"] = self.clinical_date.isoformat() if self.clinical_date else None
        d["age_days"] = (date.today() - self.clinical_date).days if self.clinical_date else None
        return d


@dataclass
class RetrievalResult:
    query: str
    evidence: list[Evidence]
    coverage: dict
    dropped: dict = field(default_factory=dict)
    timings_ms: dict = field(default_factory=dict)
    mode: str = "hybrid"


def coverage(db: Session, patient_id: uuid.UUID) -> dict:
    docs = db.scalars(select(MedicalDocument).where(MedicalDocument.patient_id == patient_id,
                                                    MedicalDocument.deleted_at.is_(None),
                                                    MedicalDocument.status == "active")).all()
    versions = {v.document_id: v for v in db.scalars(select(DocumentVersion).where(
        DocumentVersion.document_id.in_([d.id for d in docs])))
        if v.version == next(d.current_version for d in docs if d.id == v.document_id)}
    failed = [{"document_id": str(d.id), "title": d.title, "clinical_date": d.clinical_date.isoformat() if d.clinical_date else None,
               "error": versions[d.id].ingestion_error} for d in docs
              if d.id in versions and versions[d.id].ingestion_status == "failed"]
    indexed = sum(1 for d in docs if d.id in versions and versions[d.id].ingestion_status == "indexed")
    return {"documents_on_file": len(docs), "indexed": indexed, "failed": failed,
            "not_yet_indexed": len(docs) - indexed - len(failed)}


VARIANTS = ("hybrid_rerank", "hybrid", "bm25", "dense")  # the evaluation compares them; the product uses the first


def retrieve(db: Session, *, patient_id: uuid.UUID, query: str, limit: int = 6, encounter_id: uuid.UUID | None = None,
             document_types: list[str] | None = None, variant: str = "hybrid_rerank") -> RetrievalResult:
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a server-resolved UUID")
    cov = coverage(db, patient_id)
    timings: dict[str, int] = {}
    if cov["indexed"] == 0:
        return RetrievalResult(query, [], cov, timings_ms=timings)
    # Lexical: exact BM25 over this patient's current, active chunks (never stale, never another patient's).
    t0 = time.perf_counter()
    corpus = db.execute(select(DocumentChunk.id, DocumentChunk.text)
                        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                        .join(MedicalDocument, MedicalDocument.id == DocumentVersion.document_id)
                        .where(DocumentChunk.patient_id == patient_id, MedicalDocument.patient_id == patient_id,
                               MedicalDocument.deleted_at.is_(None), MedicalDocument.status == "active",
                               DocumentVersion.version == MedicalDocument.current_version,
                               *([MedicalDocument.document_type.in_(document_types)] if document_types else []))).all()
    lex = ([x.key for x in lexical.BM25({str(cid): text for cid, text in corpus}).search(query, CANDIDATES)]
           if variant != "dense" else [])
    timings["bm25"] = int((time.perf_counter() - t0) * 1000)
    # Dense: bge-m3 embedding + Qdrant, filtered to this patient.
    dense, mode = [], "hybrid"
    try:
        if variant == "bm25":
            raise RetrievalUnavailable("variant")
        t1 = time.perf_counter()
        vectors, _ = get_llm().get_embeddings([query])
        timings["embed"] = int((time.perf_counter() - t1) * 1000)
        t1 = time.perf_counter()
        dense = [h.chunk_id for h in vector_index.search(patient_id=patient_id, query=query, query_vector=vectors[0],
                                                         limit=CANDIDATES, encounter_id=encounter_id,
                                                         document_types=document_types)]
        timings["dense"] = int((time.perf_counter() - t1) * 1000)
    except (ProviderError, Exception) as exc:  # noqa: BLE001 - embedding model or Qdrant down
        if variant == "dense":
            raise RetrievalUnavailable("index_unavailable") from exc
        if not lex and variant != "bm25":
            code = exc.code if isinstance(exc, ProviderError) else type(exc).__name__
            raise RetrievalUnavailable(f"index_unavailable:{code}") from exc
        mode = "lexical_only"
    fused = lexical.rrf(lex, dense)
    ranks = {cid: {"bm25": lex.index(cid) + 1 if cid in lex else None,
                   "dense": dense.index(cid) + 1 if cid in dense else None} for cid, _ in fused}
    # Re-rank the fused top candidates; the re-ranker's order is a third vote in the fusion.
    top = [cid for cid, _ in fused[:RERANK_TOP]]
    texts = dict((str(cid), text) for cid, text in corpus)
    try:
        if variant != "hybrid_rerank":
            raise rerank.RerankerUnavailable("variant")
        t2 = time.perf_counter()
        scores = rerank.rerank(query, [texts.get(cid, "") for cid in top]) if top else []
        reranked = [cid for _, cid in sorted(zip(scores, top), key=lambda x: -x[0])]
        timings["rerank"] = int((time.perf_counter() - t2) * 1000)
        fused = lexical.rrf(lex, dense, reranked)
        for i, cid in enumerate(reranked, 1):
            ranks.setdefault(cid, {})["rerank"] = i
    except rerank.RerankerUnavailable as exc:
        timings["rerank"] = f"unavailable:{exc}"  # type: ignore[assignment]
    hits = [vector_index.Hit(cid, round(score, 5), {}) for cid, score in fused[:limit]]

    ids = [uuid.UUID(h.chunk_id) for h in hits]
    rows = db.execute(select(DocumentChunk, DocumentVersion, MedicalDocument)
                      .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                      .join(MedicalDocument, MedicalDocument.id == DocumentVersion.document_id)
                      .where(DocumentChunk.id.in_(ids))).all() if ids else []
    by_id = {r[0].id: r for r in rows}
    dropped = {"other_patient": 0, "stale_version": 0, "missing": 0, "inactive_document": 0}
    evidence: list[Evidence] = []
    for h in hits:
        row = by_id.get(uuid.UUID(h.chunk_id))
        if row is None:
            dropped["missing"] += 1
            continue
        chunk, version, doc = row
        if chunk.patient_id != patient_id or doc.patient_id != patient_id:
            dropped["other_patient"] += 1
            continue
        if version.version != doc.current_version:
            dropped["stale_version"] += 1
            continue
        if doc.deleted_at is not None or doc.status != "active":
            dropped["inactive_document"] += 1
            continue
        evidence.append(Evidence(
            evidence_id=f"document:{doc.id}@v{version.version}#chunk-{chunk.chunk_index}", document_id=str(doc.id),
            document_version=version.version, title=doc.title, document_type=doc.document_type,
            clinical_date=doc.clinical_date, source_name=doc.source_name, section=chunk.section, page=chunk.page_from,
            text=phi.scan(chunk.text).text, score=round(h.score, 4),
            source_ref=f"document:{doc.id}@v{version.version}#chunk-{chunk.chunk_index}",
            ranks=ranks.get(h.chunk_id, {})))
    return RetrievalResult(query, evidence, cov, {k: v for k, v in dropped.items() if v}, timings, mode)


def patient_document_count(db: Session, patient_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(MedicalDocument).where(MedicalDocument.patient_id == patient_id)) or 0
