"""Intake workflow API: audio/transcript intake, runs, the four human gates, orders and their safety review,
the after-visit summary, and escalations."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..guardrails import phi
from ..models import (AfterVisitSummary, AgentExecution, ApprovalRecord, ConsentRecord, DocumentationChangeSet,
                      Encounter, HandoffVersion, TranscriptionRecord, WorkflowEvent, WorkflowRun)
from ..models.base import utcnow
from ..security.deps import Principal, ensure_patient_scope, require
from ..services import audit, events, storage
from .encounters import get_scoped_encounter, require_ready_for_intake

router = APIRouter(prefix="/api", tags=["workflow"])
ACTIVE_RUN = ("queued", "running", "waiting_human", "interrupted")
AUDIO_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
               "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a"}
MAX_AUDIO = 40 * 1024 * 1024


def _runner():
    from medibridge_agents import runner  # imported lazily: keeps the API importable without the agent stack

    return runner


def _run_or_404(db: Session, principal: Principal, run_id: uuid.UUID, permission: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if principal.role != "admin" or permission != "agents.monitor":
        ensure_patient_scope(db, principal, run.patient_id, permission)
    return run


def _latest_run(db: Session, encounter_id: uuid.UUID) -> WorkflowRun | None:
    return db.scalars(select(WorkflowRun).where(WorkflowRun.encounter_id == encounter_id)
                      .order_by(WorkflowRun.created_at.desc())).first()


def _resume_if_waiting(db: Session, encounter_id: uuid.UUID, gate: str, payload: dict, wait: bool = False) -> str:
    run = _latest_run(db, encounter_id)
    if run is None or run.status != "waiting_human" or run.waiting_gate != gate:
        return "not_waiting"
    try:
        _runner().resume(run.id, payload, wait=wait)
    except _runner().RunBusy:
        raise HTTPException(409, detail={"code": "run_busy", "message": "The workflow is busy; try again shortly."})
    return "resumed"


def _audio_consent(db: Session, e: Encounter) -> ConsentRecord | None:
    return db.scalars(select(ConsentRecord).where(ConsentRecord.encounter_id == e.id,
                                                  ConsentRecord.consent_type == "audio_recording")
                      .order_by(ConsentRecord.recorded_at.desc())).first()


# --------------------------------------------------------------------------- intake input

@router.post("/encounters/{encounter_id}/intake/audio", status_code=201)
async def upload_audio(encounter_id: uuid.UUID, file: UploadFile = File(...),
                       principal: Principal = Depends(require("intake.record")), db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "intake.record")
    require_ready_for_intake(db, e)
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in AUDIO_TYPES:
        raise HTTPException(415, detail={"code": "unsupported_audio", "message": "Upload WebM/Ogg/WAV/MP3/M4A audio."})
    data = await file.read(MAX_AUDIO + 1)
    if len(data) > MAX_AUDIO or len(data) < 1000:
        raise HTTPException(413 if len(data) > MAX_AUDIO else 422, detail={"code": "bad_audio_size"})
    key, digest = storage.put(data, AUDIO_TYPES[ctype], kind="audio")
    consent = _audio_consent(db, e)
    rec = TranscriptionRecord(id=uuid.uuid4(), patient_id=e.patient_id, encounter_id=e.id,
                              consent_record_id=consent.id if consent else None, audio_storage_key=key, audio_sha256=digest,
                              status="pending", created_by=principal.user_id)
    db.add(rec)
    audit.record(db, action="intake.audio_recorded", category="modification", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="transcription", resource_id=rec.id,
                 details={"bytes": len(data), "format": AUDIO_TYPES[ctype]}, **principal.audit_ctx())
    db.commit()
    return {"transcription_id": str(rec.id), "status": rec.status}


class ManualSegment(BaseModel):
    speaker: Literal["patient", "clinician", "unattributed"] = "unattributed"
    text: str = Field(min_length=1, max_length=2000)


class ManualTranscriptIn(BaseModel):
    segments: list[ManualSegment] = Field(min_length=1, max_length=200)


@router.post("/encounters/{encounter_id}/intake/manual-transcript", status_code=201)
def manual_transcript(encounter_id: uuid.UUID, body: ManualTranscriptIn,
                      principal: Principal = Depends(require("intake.record")), db: Session = Depends(get_db)) -> dict:
    """Degraded mode when audio/ASR is unavailable: the operator types the conversation (marked as manual)."""
    e = get_scoped_encounter(db, principal, encounter_id, "intake.record")
    require_ready_for_intake(db, e)
    segs = [{"id": f"seg-{i + 1}", "start": None, "end": None, "text": s.text.strip(), "speaker": s.speaker,
             "words": [], "uncertain": False, "uncertain_reasons": [], "source": "manual_transcript"}
            for i, s in enumerate(body.segments)]
    rec = TranscriptionRecord(id=uuid.uuid4(), patient_id=e.patient_id, encounter_id=e.id, raw_segments=segs,
                              status="manual", model="manual_transcript", created_by=principal.user_id)
    db.add(rec)
    audit.record(db, action="intake.manual_transcript", category="modification", outcome="success",
                 patient_id=e.patient_id, encounter_id=e.id, resource_type="transcription", resource_id=rec.id,
                 details={"segments": len(segs)}, **principal.audit_ctx())
    db.commit()
    return {"transcription_id": str(rec.id), "status": rec.status}


# --------------------------------------------------------------------------- runs

class StartRunIn(BaseModel):
    transcription_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=80)


def run_view(db: Session, run: WorkflowRun, *, executions: bool = False) -> dict:
    out = {"id": str(run.id), "encounter_id": str(run.encounter_id), "patient_id": str(run.patient_id),
           "status": run.status, "current_node": run.current_node, "waiting_gate": run.waiting_gate,
           "error_code": run.error_code, "error_summary": run.error_summary, "created_at": run.created_at,
           "started_at": run.started_at, "ended_at": run.ended_at}
    if executions:
        rows = db.scalars(select(AgentExecution).where(AgentExecution.run_id == run.id)
                          .order_by(AgentExecution.started_at)).all()
        out["executions"] = [{"id": str(x.id), "parent_id": str(x.parent_id) if x.parent_id else None, "kind": x.kind,
                              "agent_id": x.agent_id, "node": x.node, "tool_name": x.tool_name, "attempt": x.attempt,
                              "status": x.status, "started_at": x.started_at, "ended_at": x.ended_at,
                              "duration_ms": x.duration_ms, "model": x.model, "prompt_tokens": x.prompt_tokens,
                              "completion_tokens": x.completion_tokens, "load_ms": x.load_ms, "error_code": x.error_code,
                              "error_class": x.error_class, "metrics": x.metrics} for x in rows]
        evts = db.scalars(select(WorkflowEvent).where(WorkflowEvent.run_id == run.id).order_by(WorkflowEvent.seq)).all()
        out["events"] = [events.serialize(ev) for ev in evts]
    return out


@router.post("/encounters/{encounter_id}/runs", status_code=201)
def start_run(encounter_id: uuid.UUID, body: StartRunIn, principal: Principal = Depends(require("workflow.start")),
              db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "workflow.start")
    existing = db.scalars(select(WorkflowRun).where(WorkflowRun.idempotency_key == body.idempotency_key)).first()
    if existing:  # duplicate submission: return the same run
        if existing.encounter_id != e.id:
            raise HTTPException(409, detail={"code": "idempotency_key_reused"})
        return {**run_view(db, existing), "duplicate": True}
    require_ready_for_intake(db, e)
    active = db.scalars(select(WorkflowRun).where(WorkflowRun.encounter_id == e.id,
                                                  WorkflowRun.status.in_(ACTIVE_RUN))).first()
    if active:
        raise HTTPException(409, detail={"code": "run_active", "run_id": str(active.id),
                                         "message": "This encounter already has an active workflow."})
    rec = db.get(TranscriptionRecord, body.transcription_id)
    if rec is None or rec.encounter_id != e.id:
        raise HTTPException(404, detail={"code": "transcription_not_found"})
    run = WorkflowRun(id=uuid.uuid4(), encounter_id=e.id, patient_id=e.patient_id, status="queued",
                      idempotency_key=body.idempotency_key, langgraph_thread_id=f"run-{uuid.uuid4()}",
                      started_by=principal.user_id)
    db.add(run)
    db.flush()  # the run row must exist before events reference it
    events.emit(db, "run.queued", run_id=run.id, encounter_id=e.id, patient_id=e.patient_id, agent_id="supervisor",
                status="queued", actor_user_id=principal.user_id)
    audit.record(db, action="workflow.start", category="workflow", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="workflow_run", resource_id=run.id, **principal.audit_ctx())
    db.commit()
    _runner().start(run, rec.id)
    return run_view(db, run)


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, principal: Principal = Depends(require("agents.monitor")),
            db: Session = Depends(get_db)) -> dict:
    run = _run_or_404(db, principal, run_id, "agents.monitor")
    return run_view(db, run, executions=True)


@router.get("/encounters/{encounter_id}/runs")
def encounter_runs(encounter_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
                   db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "encounter.read")
    runs = db.scalars(select(WorkflowRun).where(WorkflowRun.encounter_id == e.id).order_by(WorkflowRun.created_at.desc())).all()
    return {"items": [run_view(db, r) for r in runs]}


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_interrupted(run_id: uuid.UUID, principal: Principal = Depends(require("workflow.start")),
                       db: Session = Depends(get_db)) -> dict:
    run = _run_or_404(db, principal, run_id, "workflow.start")
    if run.status != "interrupted":
        raise HTTPException(409, detail={"code": "not_interrupted", "message": f"Run is {run.status}."})
    try:
        _runner().continue_interrupted(run.id)
    except _runner().RunBusy:
        raise HTTPException(409, detail={"code": "run_busy"})
    audit.record(db, action="workflow.resume", category="workflow", outcome="success", patient_id=run.patient_id,
                 encounter_id=run.encounter_id, resource_type="workflow_run", resource_id=run.id, **principal.audit_ctx())
    db.commit()
    return {"status": "resuming"}


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, body: CancelIn, principal: Principal = Depends(require("workflow.start")),
               db: Session = Depends(get_db)) -> dict:
    """Abandon a paused or interrupted run (nothing it prepared is written to the record)."""
    run = _run_or_404(db, principal, run_id, "workflow.start")
    if run.status not in ("waiting_human", "interrupted", "queued"):
        raise HTTPException(409, detail={"code": "not_cancellable", "message": f"Run is {run.status}."})
    if _runner().is_active(run.id):
        raise HTTPException(409, detail={"code": "run_busy"})
    run.status, run.waiting_gate, run.ended_at, run.error_code = "cancelled", None, utcnow(), "cancelled_by_user"
    events.emit(db, "run.cancelled", run_id=run.id, encounter_id=run.encounter_id, patient_id=run.patient_id,
                agent_id="supervisor", node=run.current_node, status="cancelled", actor_user_id=principal.user_id)
    audit.record(db, action="workflow.cancel", category="workflow", outcome="success", patient_id=run.patient_id,
                 encounter_id=run.encounter_id, resource_type="workflow_run", resource_id=run.id,
                 details={"reason_length": len(body.reason)}, **principal.audit_ctx())
    db.commit()
    return {"status": "cancelled"}


# --------------------------------------------------------------------------- gate 1: transcript review

class ReviewedSegment(BaseModel):
    id: str = Field(max_length=20)
    speaker: Literal["patient", "clinician", "unattributed"]
    text: str = Field(max_length=2000)


class TranscriptReviewIn(BaseModel):
    segments: list[ReviewedSegment] = Field(min_length=1, max_length=400)
    confirm: bool = False


@router.get("/transcriptions/{transcription_id}")
def get_transcription(transcription_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
                      db: Session = Depends(get_db)) -> dict:
    rec = db.get(TranscriptionRecord, transcription_id)
    if rec is None:
        raise HTTPException(404, detail={"code": "not_found"})
    ensure_patient_scope(db, principal, rec.patient_id, "encounter.read")
    audit.record(db, action="transcript.read", category="access", outcome="allowed", patient_id=rec.patient_id,
                 encounter_id=rec.encounter_id, resource_type="transcription", resource_id=rec.id, **principal.audit_ctx())
    db.commit()
    return {"id": str(rec.id), "encounter_id": str(rec.encounter_id), "status": rec.status, "model": rec.model,
            "language": rec.language, "audio_duration_s": float(rec.audio_duration_s) if rec.audio_duration_s else None,
            "raw_segments": rec.raw_segments, "reviewed_segments": rec.reviewed_segments,
            "reviewed_at": rec.reviewed_at, "error_code": rec.error_code, "has_audio": bool(rec.audio_storage_key)}


@router.post("/transcriptions/{transcription_id}/review")
def review_transcription(transcription_id: uuid.UUID, body: TranscriptReviewIn,
                         principal: Principal = Depends(require("transcript.review")), db: Session = Depends(get_db)) -> dict:
    rec = db.get(TranscriptionRecord, transcription_id)
    if rec is None:
        raise HTTPException(404, detail={"code": "not_found"})
    ensure_patient_scope(db, principal, rec.patient_id, "transcript.review")
    if rec.status not in ("needs_review", "manual", "reviewed"):
        raise HTTPException(409, detail={"code": "not_reviewable", "message": f"Transcript is {rec.status}."})
    original = {s["id"]: s for s in rec.raw_segments}
    if any(s.id not in original for s in body.segments):
        raise HTTPException(422, detail={"code": "unknown_segment"})
    reviewed, corrected = [], 0
    for s in body.segments:
        raw = original[s.id]
        changed = s.text.strip() != raw["text"].strip()
        corrected += changed
        reviewed.append({"id": s.id, "speaker": s.speaker, "text": s.text.strip(), "start": raw.get("start"),
                         "end": raw.get("end"), "original_text": raw["text"] if changed else None,
                         "corrected_by": str(principal.user_id) if changed else None})
    rec.reviewed_segments = reviewed  # raw_segments is never modified: the original transcript is preserved
    if body.confirm:
        rec.status, rec.reviewed_by, rec.reviewed_at = "reviewed", principal.user_id, utcnow()
    audit.record(db, action="transcript.reviewed" if body.confirm else "transcript.edited", category="modification",
                 outcome="success", patient_id=rec.patient_id, encounter_id=rec.encounter_id,
                 resource_type="transcription", resource_id=rec.id,
                 details={"segments": len(reviewed), "corrected": corrected,
                          "speakers": sorted({s["speaker"] for s in reviewed})}, **principal.audit_ctx())
    db.commit()
    resumed = "not_waiting"
    if body.confirm:
        resumed = _resume_if_waiting(db, rec.encounter_id, "transcript_review",
                                     {"action": "confirm", "actor_user_id": str(principal.user_id),
                                      "actor_role": principal.role})
    return {"status": rec.status, "corrected": corrected, "workflow": resumed}


# --------------------------------------------------------------------------- gate 2: handoff review

def _handoff_payload(db: Session, encounter_id: uuid.UUID, version: int | None = None) -> dict:
    from medibridge_agents import handoff_store

    report = handoff_store.get_report(db, encounter_id)
    if report is None:
        raise HTTPException(404, detail={"code": "no_handoff"})
    versions = db.scalars(select(HandoffVersion).where(HandoffVersion.handoff_id == report.id)
                          .order_by(HandoffVersion.version)).all()
    v = next((x for x in versions if x.version == (version or report.current_version)), None)
    if v is None:
        raise HTTPException(404, detail={"code": "version_not_found"})
    return {"handoff_id": str(report.id), "status": report.status, "current_version": report.current_version,
            "approved_version": report.approved_version, "approved_at": report.approved_at,
            "version": v.version, "content_sha256": v.content_sha256, "content": v.content,
            "evidence_bundle": v.evidence_bundle, "author_kind": v.author_kind, "model": v.model,
            "prompt_version": v.prompt_version, "validation": v.validation, "created_at": v.created_at,
            "versions": [{"version": x.version, "author_kind": x.author_kind, "created_at": x.created_at,
                          "content_sha256": x.content_sha256, "change_summary": x.change_summary} for x in versions]}


@router.get("/encounters/{encounter_id}/handoff")
def get_handoff(encounter_id: uuid.UUID, version: int | None = None,
                principal: Principal = Depends(require("handoff.read")), db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "handoff.read")
    payload = _handoff_payload(db, e.id, version)
    audit.record(db, action="handoff.read", category="access", outcome="allowed", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="handoff_version", details={"version": payload["version"]},
                 **principal.audit_ctx())
    db.commit()
    return payload


class HandoffEditIn(BaseModel):
    base_version: int
    base_sha256: str = Field(min_length=64, max_length=64)
    sections: list[dict] = Field(min_length=1, max_length=20)
    change_summary: str = Field(min_length=3, max_length=500)


@router.post("/encounters/{encounter_id}/handoff/edit", status_code=201)
def edit_handoff(encounter_id: uuid.UUID, body: HandoffEditIn, principal: Principal = Depends(require("handoff.edit")),
                 db: Session = Depends(get_db)) -> dict:
    """A doctor's edited version. Section structure and evidence links are validated; text is identifier-scanned."""
    from medibridge_agents import handoff_store

    e = get_scoped_encounter(db, principal, encounter_id, "handoff.edit")
    report = handoff_store.get_report(db, e.id)
    base = handoff_store.current_version(db, report) if report else None
    if base is None or base.version != body.base_version or base.content_sha256 != body.base_sha256:
        raise HTTPException(409, detail={"code": "stale_version", "message": "The handoff changed; reload before editing."})
    keys = [s["key"] for s in base.content["sections"]]
    if [s.get("key") for s in body.sections] != keys:
        raise HTTPException(422, detail={"code": "section_structure", "message": "Sections cannot be added or removed."})
    sections = []
    for old, new in zip(base.content["sections"], body.sections):
        items = []
        for it in new.get("items", []):
            text = str(it.get("text", "")).strip()[:1000]
            if not text:
                continue
            if phi.contains_identifiers(text):
                raise HTTPException(422, detail={"code": "identifiers", "message": "Remove contact or ID numbers."})
            ids = [i for i in it.get("evidence_ids", []) if i in base.evidence_bundle or i.split(":")[0] in
                   ("patient", "encounter", "intake")]
            prior = next((o for o in old["items"] if o.get("text") == text), None)
            items.append({**(prior or {}), "text": text, "evidence_ids": ids,
                          "author": (prior or {}).get("author", "doctor") if prior else "doctor"})
        sections.append({**old, "items": items})
    content = {**base.content, "sections": sections, "edited_by_doctor": True}
    try:
        v = handoff_store.create_version(db, encounter_id=e.id, patient_id=e.patient_id, run_id=report.run_id,
                                         content=content, evidence_bundle=base.evidence_bundle, author_kind="mixed",
                                         author_user_id=principal.user_id, model=base.model,
                                         prompt_version=base.prompt_version, validation=base.validation,
                                         change_summary=body.change_summary, status="in_review")
    except handoff_store.HandoffConflict as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)})
    audit.record(db, action="handoff.edited", category="modification", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="handoff_version", resource_id=v.id, details={"version": v.version},
                 **principal.audit_ctx())
    events.emit(db, "handoff.edited", run_id=report.run_id, encounter_id=e.id, patient_id=e.patient_id,
                agent_id="handoff_brief", status="in_review", version=v.version, actor_user_id=principal.user_id)
    db.commit()
    return _handoff_payload(db, e.id)


class HandoffDecisionIn(BaseModel):
    version: int
    expected_sha256: str = Field(min_length=64, max_length=64)
    decision: Literal["approved", "rejected", "changes_requested"]
    rationale: str = Field("", max_length=2000)
    feedback: list[str] = Field(default_factory=list, max_length=10)


@router.post("/encounters/{encounter_id}/handoff/decision")
def decide_handoff(encounter_id: uuid.UUID, body: HandoffDecisionIn,
                   principal: Principal = Depends(require("handoff.approve")), db: Session = Depends(get_db)) -> dict:
    from medibridge_agents import handoff_store

    e = get_scoped_encounter(db, principal, encounter_id, "handoff.approve")
    if body.decision in ("rejected", "changes_requested") and len(body.rationale.strip()) < 5:
        raise HTTPException(422, detail={"code": "rationale_required", "message": "Give a reason (at least 5 characters)."})
    try:
        record = handoff_store.decide(db, encounter_id=e.id, version=body.version, expected_sha256=body.expected_sha256,
                                      decision=body.decision, rationale=body.rationale, doctor_id=principal.user_id,
                                      audit_ctx=principal.audit_ctx())
        db.commit()
    except handoff_store.HandoffConflict as exc:
        db.rollback()
        raise HTTPException(409, detail={"code": exc.code, "message": str(exc)})
    action = {"approved": "approve", "rejected": "reject", "changes_requested": "regenerate"}[body.decision]
    resumed = _resume_if_waiting(db, e.id, "handoff_review",
                                 {"action": action, "actor_user_id": str(principal.user_id), "actor_role": principal.role,
                                  "feedback": [f[:300] for f in body.feedback]})
    return {"approval_record_id": str(record.id), "decision": body.decision, "workflow": resumed}


# --------------------------------------------------------------------------- gates 3-4: orders and their safety review

def _run_ctx(db: Session, e: Encounter, principal: Principal):
    from medibridge_agents.runtime import RunContext

    run = _latest_run(db, e.id)
    return RunContext(run.id if run else uuid.uuid4(), e.id, e.patient_id, principal.user_id, principal.role), run


def _changeset_view(cs: DocumentationChangeSet | None) -> dict | None:
    if cs is None:
        return None
    return {"id": str(cs.id), "status": cs.status, "content_sha256": cs.content_sha256, "content": cs.content,
            "safety_screen": cs.safety_screen, "safety_decision": cs.safety_decision, "created_at": cs.created_at,
            "committed_at": cs.committed_at}


@router.get("/encounters/{encounter_id}/documentation")
def get_documentation(encounter_id: uuid.UUID, principal: Principal = Depends(require("handoff.read")),
                      db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "handoff.read")
    run = _latest_run(db, e.id)
    draft = None
    if run:
        snap = _runner().graph().get_state({"configurable": {"thread_id": run.langgraph_thread_id}})
        draft = ((snap.values or {}).get("documentation") or {}).get("draft")
    changesets = db.scalars(select(DocumentationChangeSet).where(DocumentationChangeSet.encounter_id == e.id)
                            .order_by(DocumentationChangeSet.created_at.desc())).all()
    return {"encounter_status": e.status, "draft": draft, "run_waiting_gate": run.waiting_gate if run else None,
            "run_status": run.status if run else None, "run_current_node": run.current_node if run else None,
            "changeset": _changeset_view(changesets[0] if changesets else None),
            "history": [_changeset_view(c) for c in changesets[1:6]]}


@router.post("/encounters/{encounter_id}/documentation/prepare")
def prepare_documentation(encounter_id: uuid.UUID, body: dict, principal: Principal = Depends(require("note.write")),
                          db: Session = Depends(get_db)) -> dict:
    """Validate and stage the note and orders (structure only). Screening happens after the doctor signs."""
    from medibridge_agents import tools
    from medibridge_agents.documentation_store import DocumentationInvalid

    e = get_scoped_encounter(db, principal, encounter_id, "note.write")
    ctx, _ = _run_ctx(db, e, principal)
    try:
        return tools.invoke(ctx, "documentation.prepare_update", {"documentation": body}, caller="api:doctor")
    except DocumentationInvalid as exc:
        raise HTTPException(422, detail={"code": "documentation_invalid", "problems": exc.problems})
    except tools.ToolDenied as exc:
        raise HTTPException(403, detail={"code": exc.code, "message": str(exc)})


class SubmitIn(BaseModel):
    changeset_id: uuid.UUID
    expected_sha256: str = Field(min_length=64, max_length=64)


@router.post("/encounters/{encounter_id}/documentation/submit", status_code=202)
def submit_orders(encounter_id: uuid.UUID, body: SubmitIn, principal: Principal = Depends(require("note.approve")),
                  db: Session = Depends(get_db)) -> dict:
    """The doctor signs the exact prepared change set; Order Entry then has Medication Safety screen the orders."""
    e = get_scoped_encounter(db, principal, encounter_id, "note.approve")
    cs = db.get(DocumentationChangeSet, body.changeset_id)
    if cs is None or cs.encounter_id != e.id or cs.status != "prepared" or cs.content_sha256 != body.expected_sha256:
        raise HTTPException(409, detail={"code": "changeset_stale", "message": "Prepare the documentation again."})
    audit.record(db, action="documentation.signed", category="approval", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="documentation_changeset", resource_id=cs.id,
                 details={"sha256": cs.content_sha256[:16], "orders": len(cs.content.get("prescriptions", []))},
                 **principal.audit_ctx())
    db.commit()
    resumed = _resume_if_waiting(db, e.id, "orders_review",
                                 {"action": "submit", "changeset_id": str(cs.id), "expected_sha256": cs.content_sha256,
                                  "actor_user_id": str(principal.user_id), "actor_role": principal.role})
    if resumed != "resumed":
        raise HTTPException(409, detail={"code": "not_waiting", "message": "The workflow is not waiting for orders."})
    return {"workflow": resumed, "changeset_id": str(cs.id)}


class SafetyDecisionIn(BaseModel):
    changeset_id: uuid.UUID
    action: Literal["override", "revise"]
    reason: str = Field("", max_length=1000)
    acknowledged_alerts: list[str] = Field(default_factory=list, max_length=40)


@router.post("/encounters/{encounter_id}/documentation/safety-decision", status_code=202)
def safety_decision(encounter_id: uuid.UUID, body: SafetyDecisionIn, principal: Principal = Depends(require("note.approve")),
                    db: Session = Depends(get_db)) -> dict:
    """Blocking alerts come back to the doctor once: override (every alert acknowledged, with a reason) or revise."""
    from medibridge_agents.documentation_store import DocumentationInvalid, record_decision

    e = get_scoped_encounter(db, principal, encounter_id, "note.approve")
    run = _latest_run(db, e.id)
    if run is None or run.waiting_gate != "safety_review":
        raise HTTPException(409, detail={"code": "not_waiting", "message": "No safety review is waiting."})
    cs = db.get(DocumentationChangeSet, body.changeset_id)
    if cs is None or cs.encounter_id != e.id:
        raise HTTPException(404, detail={"code": "not_found"})
    try:
        record_decision(db, changeset_id=cs.id, doctor_id=principal.user_id, action=body.action, reason=body.reason,
                        acknowledged=body.acknowledged_alerts, audit_ctx=principal.audit_ctx())
        db.commit()
    except DocumentationInvalid as exc:
        db.rollback()
        raise HTTPException(422, detail={"code": "safety_decision_invalid", "problems": exc.problems})
    resumed = _resume_if_waiting(db, e.id, "safety_review",
                                 {"action": body.action, "actor_user_id": str(principal.user_id),
                                  "actor_role": principal.role})
    return {"workflow": resumed, "action": body.action}


# --------------------------------------------------------------------------- after-visit summary (patient-facing)

@router.get("/encounters/{encounter_id}/after-visit-summary")
def get_avs(encounter_id: uuid.UUID, principal: Principal = Depends(require("handoff.read")),
            db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "handoff.read")
    avs = db.scalars(select(AfterVisitSummary).where(AfterVisitSummary.encounter_id == e.id,
                                                     AfterVisitSummary.status != "superseded")
                     .order_by(AfterVisitSummary.created_at.desc())).first()
    if avs is None:
        return {"avs": None, "encounter_status": e.status}
    audit.record(db, action="avs.read", category="access", outcome="allowed", patient_id=e.patient_id, encounter_id=e.id,
                 resource_type="after_visit_summary", resource_id=avs.id, **principal.audit_ctx())
    db.commit()
    return {"avs": {"id": str(avs.id), "status": avs.status, "content": avs.content, "content_sha256": avs.content_sha256,
                    "created_at": avs.created_at, "released_at": avs.released_at, "validation": avs.validation},
            "encounter_status": e.status}


class AvsReleaseIn(BaseModel):
    avs_id: uuid.UUID
    expected_sha256: str = Field(min_length=64, max_length=64)


@router.post("/encounters/{encounter_id}/after-visit-summary/release")
def release_avs(encounter_id: uuid.UUID, body: AvsReleaseIn, principal: Principal = Depends(require("note.approve")),
                db: Session = Depends(get_db)) -> dict:
    """A doctor releases the exact summary version to the patient (hash-bound, audited)."""
    e = get_scoped_encounter(db, principal, encounter_id, "note.approve")
    avs = db.get(AfterVisitSummary, body.avs_id, with_for_update=True)
    if avs is None or avs.encounter_id != e.id or avs.status != "draft" or avs.content_sha256 != body.expected_sha256:
        raise HTTPException(409, detail={"code": "avs_stale", "message": "The summary changed or was already released."})
    if principal.role != "doctor":
        raise HTTPException(403, detail={"code": "doctor_required"})
    approval = ApprovalRecord(id=uuid.uuid4(), patient_id=e.patient_id, encounter_id=e.id, subject_type="after_visit_summary",
                              subject_id=avs.id, subject_sha256=avs.content_sha256, decision="approved",
                              rationale="Released to the patient.", decided_by=principal.user_id, decided_role="doctor")
    db.add(approval)
    avs.status, avs.released_by, avs.released_at = "released", principal.user_id, utcnow()
    audit.record(db, action="avs.released", category="approval", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="after_visit_summary", resource_id=avs.id,
                 details={"sha256": avs.content_sha256[:16]}, **principal.audit_ctx())
    events.emit(db, "avs.released", encounter_id=e.id, patient_id=e.patient_id, agent_id="after_visit_summary",
                status="released", actor_user_id=principal.user_id)
    db.commit()
    return {"status": "released", "released_at": avs.released_at}


# --------------------------------------------------------------------------- escalations

class EscalationAckIn(BaseModel):
    rule_id: str = Field(max_length=80)
    note: str = Field("", max_length=500)


@router.post("/encounters/{encounter_id}/escalations/ack")
def acknowledge_escalation(encounter_id: uuid.UUID, body: EscalationAckIn,
                           principal: Principal = Depends(require("encounter.read")), db: Session = Depends(get_db)) -> dict:
    """Only a clinician (doctor or nurse in the care team) can acknowledge a raised flag."""
    if principal.role not in ("doctor", "nurse"):
        raise HTTPException(403, detail={"code": "clinician_required"})
    e = get_scoped_encounter(db, principal, encounter_id, "encounter.read")
    run = _latest_run(db, e.id)
    events.emit(db, "escalation.acknowledged", run_id=run.id if run else None, encounter_id=e.id, patient_id=e.patient_id,
                agent_id="supervisor", status="acknowledged", rule_id=body.rule_id, actor_user_id=principal.user_id)
    audit.record(db, action="escalation.acknowledged", category="safety", outcome="success", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="escalation", resource_id=body.rule_id,
                 details={"has_note": bool(body.note)}, **principal.audit_ctx())
    db.commit()
    return {"rule_id": body.rule_id, "status": "acknowledged"}


@router.get("/encounters/{encounter_id}/escalations")
def list_escalations(encounter_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
                     db: Session = Depends(get_db)) -> dict:
    e = get_scoped_encounter(db, principal, encounter_id, "encounter.read")
    raised = db.scalars(select(WorkflowEvent).where(WorkflowEvent.encounter_id == e.id,
                                                    WorkflowEvent.type == "escalation.raised")).all()
    acks = {ev.attrs.get("rule_id"): ev for ev in db.scalars(select(WorkflowEvent).where(
        WorkflowEvent.encounter_id == e.id, WorkflowEvent.type == "escalation.acknowledged"))}
    run = _latest_run(db, e.id)
    details = []
    if run:
        snap = _runner().graph().get_state({"configurable": {"thread_id": run.langgraph_thread_id}})
        details = (snap.values or {}).get("escalations") or []
    rules = {r for ev in raised for r in ev.attrs.get("rules", [])}
    return {"items": [{**d, "status": "acknowledged" if d["rule_id"] in acks else "open",
                       "acknowledged_at": acks[d["rule_id"]].created_at if d["rule_id"] in acks else None}
                      for d in details if d["rule_id"] in rules]}


# --------------------------------------------------------------------------- encounter workspace

@router.get("/encounters/{encounter_id}/workflow")
def workflow_state(encounter_id: uuid.UUID, principal: Principal = Depends(require("encounter.read")),
                   db: Session = Depends(get_db)) -> dict:
    """Clinician view of the latest run: findings with provenance, escalations, conflicts, missing information."""
    e = get_scoped_encounter(db, principal, encounter_id, "encounter.read")
    run = _latest_run(db, e.id)
    if run is None:
        return {"run": None}
    values = (_runner().graph().get_state({"configurable": {"thread_id": run.langgraph_thread_id}}).values or {})
    acks = {ev.attrs.get("rule_id") for ev in db.scalars(select(WorkflowEvent).where(
        WorkflowEvent.encounter_id == e.id, WorkflowEvent.type == "escalation.acknowledged"))}
    history = values.get("history") or {}
    documents = values.get("documents") or {}
    med = values.get("medication_safety") or {}
    audit.record(db, action="workflow.read", category="access", outcome="allowed", patient_id=e.patient_id,
                 encounter_id=e.id, resource_type="workflow_run", resource_id=run.id, **principal.audit_ctx())
    db.commit()
    return {
        "run": run_view(db, run),
        "transcription_id": values.get("transcription_id"),
        "findings": values.get("findings"),
        "escalations": [{**f, "status": "acknowledged" if f["rule_id"] in acks else "open"}
                        for f in values.get("escalations") or []],
        "conflicts": history.get("conflicts", []),
        "missing_documents": history.get("missing_documents", []),
        "medication_list_state": history.get("medication_list_state"),
        "prior_encounters": history.get("prior_encounters", [])[:10],
        "results": history.get("results", []),
        "documents": {"status": documents.get("status"), "coverage": documents.get("coverage"), "mode": documents.get("mode"),
                      "queries": documents.get("queries", []), "unavailable_reason": documents.get("unavailable_reason"),
                      "evidence": [{k: ev.get(k) for k in ("evidence_id", "title", "clinical_date", "section", "score",
                                                         "document_id", "text", "age_days", "ranks")}
                                   for ev in documents.get("evidence", [])]},
        "medication_safety": {"status": med.get("status"), "alerts": med.get("alerts", []),
                              "completeness": med.get("completeness"), "statement": med.get("statement"),
                              "release": med.get("release")},
        "outcome": values.get("outcome"),
    }
