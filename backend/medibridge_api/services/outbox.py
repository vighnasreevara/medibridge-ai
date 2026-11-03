"""Transactional outbox: side effects that must follow a committed change (indexing, approved-note indexing).

Producers insert an OutboxItem in the same transaction as the change. A single background worker claims items
with SELECT ... FOR UPDATE SKIP LOCKED, runs the handler, and retries with backoff up to MAX_ATTEMPTS.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_sessionmaker
from ..models import OutboxItem
from ..models.base import utcnow

log = logging.getLogger("medibridge.outbox")
MAX_ATTEMPTS = 5
HANDLERS: dict[str, Callable[[dict], dict]] = {}


def handler(kind: str):
    def register(fn: Callable[[dict], dict]):
        HANDLERS[kind] = fn
        return fn
    return register


def enqueue(db: Session, kind: str, payload: dict) -> OutboxItem:
    item = OutboxItem(id=uuid.uuid4(), kind=kind, payload=payload)
    db.add(item)
    return item


def process_one() -> bool:
    with get_sessionmaker()() as db:
        item = db.scalars(
            select(OutboxItem).where(or_(OutboxItem.status == "pending",
                                         (OutboxItem.status == "failed") & (OutboxItem.attempts < MAX_ATTEMPTS)))
            .order_by(OutboxItem.created_at).limit(1).with_for_update(skip_locked=True)).first()
        if item is None:
            return False
        if item.status == "failed" and item.processed_at and \
                utcnow() - item.processed_at < timedelta(seconds=10 * 2 ** item.attempts):
            return False  # backoff
        item.status, item.attempts = "processing", item.attempts + 1
        db.commit()
        kind, payload, item_id = item.kind, dict(item.payload), item.id
    try:
        result = HANDLERS[kind](payload)
        ok = result.get("status") not in ("failed",)
        error = None if ok else str(result.get("error"))[:500]
    except Exception as exc:  # noqa: BLE001
        ok, error = False, f"{type(exc).__name__}"
        log.exception("outbox handler %s failed", kind)
    with get_sessionmaker()() as db:
        item = db.get(OutboxItem, item_id)
        item.status, item.last_error, item.processed_at = ("done" if ok else "failed"), error, utcnow()
        db.commit()
    return True


class Worker:
    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="medibridge-outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = process_one()
            except Exception:  # noqa: BLE001 - database briefly unavailable; keep the worker alive
                log.exception("outbox loop error")
                busy = False
            if not busy:
                self._stop.wait(self.interval)


@handler("ingest_document_version")
def _ingest(payload: dict) -> dict:
    from .ingestion import ingest_version

    return ingest_version(uuid.UUID(payload["version_id"]))


@handler("index_clinical_note")
def _index_note(payload: dict) -> dict:
    """Store an approved note as a protected text document and index it for future retrieval."""
    from datetime import date

    from ..guardrails import phi
    from ..models import ClinicalNote, ClinicalNoteVersion, DocumentVersion, MedicalDocument
    from . import storage
    from .ingestion import ingest_version

    with get_sessionmaker()() as db:
        note = db.get(ClinicalNote, uuid.UUID(payload["note_id"]))
        version = db.scalars(select(ClinicalNoteVersion).where(ClinicalNoteVersion.note_id == note.id)
                             .order_by(ClinicalNoteVersion.version.desc())).first()
        existing = db.scalars(select(MedicalDocument).where(MedicalDocument.encounter_id == note.encounter_id,
                                                            MedicalDocument.document_type == "clinical_note",
                                                            MedicalDocument.title == note.title)).first()
        if existing:
            version_id = db.scalars(select(DocumentVersion.id).where(DocumentVersion.document_id == existing.id)).first()
        else:
            text = f"{note.title}\n\n" + phi.scan(version.body).text
            key, digest = storage.put(text.encode("utf-8"), "txt")
            doc = MedicalDocument(id=uuid.uuid4(), patient_id=note.patient_id, encounter_id=note.encounter_id,
                                  document_type="clinical_note", title=note.title, clinical_date=date.today(),
                                  source_name="MediBridge approved documentation", created_by=note.author_user_id)
            db.add(doc)
            db.flush()
            v = DocumentVersion(id=uuid.uuid4(), document_id=doc.id, version=1, storage_key=key, content_sha256=digest,
                                mime_type="text/plain", size_bytes=len(text.encode("utf-8")),
                                created_by=note.author_user_id, change_reason=f"note:{note.id}@v{version.version}")
            db.add(v)
            db.commit()
            version_id = v.id
    return ingest_version(version_id)
