"""Qdrant index for medical-document chunks.

- Versioned collection ``medical_docs__<embedding model>__v<schema>`` behind the alias ``medical_docs``;
  reindexing builds a new collection and swaps the alias atomically.
- Points carry minimal metadata (ids, type, date, source ref, embedding model) and NO clinical text: displayed
  text is always re-read from Postgres after authorization.
- Dense bge-m3 vectors only. The lexical half of hybrid retrieval is an exact BM25 over the patient's chunks
  (services/lexical.py); the two rankings are fused in services/retrieval.py.
- ``search`` cannot be called without a patient id; the patient filter is built here, not by callers or LLMs.
"""
from __future__ import annotations

import re
import uuid
import warnings
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from qdrant_client import QdrantClient, models

from ..config import get_settings

ALIAS = "medical_docs"
SCHEMA_VERSION = 2
DENSE = "dense"


@lru_cache
def client() -> QdrantClient:
    s = get_settings()
    # Qdrant is reached over loopback only (compose binds 127.0.0.1), so plain HTTP with an API key is expected.
    warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key.get_secret_value(), timeout=30,
                        prefer_grpc=False, https=False)


def collection_name(model: str | None = None, version: int = SCHEMA_VERSION) -> str:
    model = re.sub(r"[^a-z0-9]+", "-", (model or get_settings().embedding_model).lower()).strip("-")
    return f"{ALIAS}__{model}__v{version}"


def ensure_collection(dim: int, name: str | None = None) -> str:
    name = name or collection_name()
    c = client()
    if not c.collection_exists(name):
        c.create_collection(
            name,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        )
        c.create_payload_index(name, "patient_id", models.KeywordIndexParams(type="keyword", is_tenant=True))
        for field in ("encounter_id", "document_id", "document_type", "embedding_model"):
            c.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
        c.create_payload_index(name, "document_version", models.PayloadSchemaType.INTEGER)
        c.create_payload_index(name, "clinical_date_ord", models.PayloadSchemaType.INTEGER)
    aliases = {a.alias_name: a.collection_name for a in c.get_aliases().aliases}
    if aliases.get(ALIAS) != name:
        ops = []
        if ALIAS in aliases:
            ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=ALIAS)))
        ops.append(models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=name, alias_name=ALIAS)))
        c.update_collection_aliases(change_aliases_operations=ops)
    return name


@dataclass
class ChunkPoint:
    chunk_id: uuid.UUID
    patient_id: uuid.UUID
    encounter_id: uuid.UUID | None
    document_id: uuid.UUID
    document_version: int
    document_type: str
    clinical_date: date | None
    source_ref: str
    text: str  # used to build vectors only; never stored in the payload


def upsert(points: list[ChunkPoint], vectors: list[list[float]], model: str) -> None:
    name = ensure_collection(len(vectors[0]))
    client().upsert(name, wait=True, points=[
        models.PointStruct(
            id=str(p.chunk_id),
            vector={DENSE: v},
            payload={"patient_id": str(p.patient_id), "encounter_id": str(p.encounter_id) if p.encounter_id else None,
                     "document_id": str(p.document_id), "document_version": p.document_version,
                     "chunk_id": str(p.chunk_id), "document_type": p.document_type,
                     "clinical_date": p.clinical_date.isoformat() if p.clinical_date else None,
                     "clinical_date_ord": p.clinical_date.toordinal() if p.clinical_date else 0,
                     "source_ref": p.source_ref, "embedding_model": model},
        ) for p, v in zip(points, vectors)])


def delete_document_version(document_id: uuid.UUID, version: int) -> None:
    c = client()
    if not c.collection_exists(collection_name()):
        return
    c.delete(ALIAS, points_selector=models.FilterSelector(filter=models.Filter(must=[
        models.FieldCondition(key="document_id", match=models.MatchValue(value=str(document_id))),
        models.FieldCondition(key="document_version", match=models.MatchValue(value=version))])), wait=True)


@dataclass
class Hit:
    chunk_id: str
    score: float
    payload: dict


def search(*, patient_id: uuid.UUID, query: str, query_vector: list[float], limit: int = 8,
           encounter_id: uuid.UUID | None = None, document_types: list[str] | None = None,
           since: date | None = None) -> list[Hit]:
    """Dense search restricted to ONE patient. ``patient_id`` must be the server-resolved UUID."""
    if not isinstance(patient_id, uuid.UUID):
        raise TypeError("patient_id must be a server-resolved UUID")
    must = [models.FieldCondition(key="patient_id", match=models.MatchValue(value=str(patient_id))),
            models.FieldCondition(key="embedding_model", match=models.MatchValue(value=get_settings().embedding_model))]
    if encounter_id:
        must.append(models.FieldCondition(key="encounter_id", match=models.MatchValue(value=str(encounter_id))))
    if document_types:
        must.append(models.FieldCondition(key="document_type", match=models.MatchAny(any=document_types)))
    if since:
        must.append(models.FieldCondition(key="clinical_date_ord", range=models.Range(gte=since.toordinal())))
    flt = models.Filter(must=must)
    result = client().query_points(ALIAS, query=query_vector, using=DENSE, query_filter=flt, limit=limit,
                                   with_payload=True)
    hits = [Hit(str(p.id), p.score, p.payload or {}) for p in result.points]
    # Defence in depth: never return a point for another patient, whatever the index contains.
    return [h for h in hits if h.payload.get("patient_id") == str(patient_id)]


def stats() -> dict:
    c = client()
    aliases = {a.alias_name: a.collection_name for a in c.get_aliases().aliases}
    name = aliases.get(ALIAS)
    if not name:
        return {"alias": ALIAS, "collection": None, "points": 0}
    info = c.get_collection(name)
    return {"alias": ALIAS, "collection": name, "points": info.points_count, "status": str(info.status)}
