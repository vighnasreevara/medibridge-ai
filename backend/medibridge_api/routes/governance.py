"""Audit & governance (read-only) and administration."""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import REPO_ROOT, get_settings
from ..db import get_db
from ..models import (AuditLog, CareTeamAssignment, DocumentVersion, OutboxItem, Patient, Role, User)
from ..models.base import utcnow
from ..security import passwords, sessions
from ..security.deps import Principal, require
from ..services import audit
from ..services.local_http import client as http

router = APIRouter(prefix="/api", tags=["governance"])
RUNTIME_SETTINGS = REPO_ROOT / "var" / "runtime-settings.json"


# --------------------------------------------------------------------------- audit

@router.get("/audit")
def audit_log(category: str | None = None, action: str | None = Query(None, max_length=80),
              outcome: str | None = None, patient_id: uuid.UUID | None = None, actor_user_id: uuid.UUID | None = None,
              since_hours: int = Query(24 * 30, ge=1, le=24 * 365), before_seq: int | None = None,
              limit: int = Query(100, ge=1, le=500), principal: Principal = Depends(require("audit.read")),
              db: Session = Depends(get_db)) -> dict:
    stmt = select(AuditLog).where(AuditLog.occurred_at >= utcnow() - timedelta(hours=since_hours))
    if category:
        stmt = stmt.where(AuditLog.category.in_(category.split(",")))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if outcome:
        stmt = stmt.where(AuditLog.outcome.in_(outcome.split(",")))
    if patient_id:
        stmt = stmt.where(AuditLog.patient_id == patient_id)
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if before_seq:
        stmt = stmt.where(AuditLog.seq < before_seq)
    rows = db.scalars(stmt.order_by(AuditLog.seq.desc()).limit(limit)).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    mrns = dict(db.execute(select(Patient.id, Patient.mrn).where(Patient.id.in_({r.patient_id for r in rows if r.patient_id}))).all())
    audit.record(db, action="audit.read", category="access", outcome="allowed", resource_type="audit_log",
                 details={"results": len(rows)}, **principal.audit_ctx())
    db.commit()
    return {"items": [{"seq": r.seq, "occurred_at": r.occurred_at, "action": r.action, "category": r.category,
                       "outcome": r.outcome, "actor": names.get(r.actor_user_id) or (r.actor_role or "system"),
                       "actor_role": r.actor_role, "patient_mrn": mrns.get(r.patient_id),
                       "encounter_id": str(r.encounter_id) if r.encounter_id else None,
                       "resource_type": r.resource_type, "resource_id": r.resource_id, "ip": r.ip,
                       "details": r.details, "row_hash": r.row_hash[:16]} for r in rows],
            "next_before_seq": rows[-1].seq if len(rows) == limit else None}


@router.get("/audit/verify")
def verify(principal: Principal = Depends(require("audit.read")), db: Session = Depends(get_db)) -> dict:
    result = audit.verify_chain(db)
    audit.record(db, action="audit.verify", category="security", outcome="success" if result["intact"] else "failure",
                 resource_type="audit_log", details={"intact": result["intact"], "last_seq": result["last_seq"]},
                 **principal.audit_ctx())
    db.commit()
    return result


@router.get("/audit/summary")
def summary(since_hours: int = Query(24 * 7, ge=1, le=24 * 365), principal: Principal = Depends(require("audit.read")),
            db: Session = Depends(get_db)) -> dict:
    since = utcnow() - timedelta(hours=since_hours)
    by = db.execute(select(AuditLog.category, AuditLog.outcome, func.count()).where(AuditLog.occurred_at >= since)
                    .group_by(AuditLog.category, AuditLog.outcome)).all()
    top = db.execute(select(AuditLog.action, func.count()).where(AuditLog.occurred_at >= since)
                     .group_by(AuditLog.action).order_by(func.count().desc()).limit(15)).all()
    day = func.date_trunc("day", AuditLog.occurred_at)
    daily = db.execute(select(day, AuditLog.outcome, func.count()).where(AuditLog.occurred_at >= since)
                       .group_by(day, AuditLog.outcome).order_by(day)).all()
    return {"by_category": [{"category": c, "outcome": o, "count": n} for c, o, n in by],
            "top_actions": [{"action": a, "count": n} for a, n in top],
            "daily": [{"day": d.date().isoformat(), "outcome": o, "count": n} for d, o, n in daily]}


# --------------------------------------------------------------------------- users & care teams

class UserIn(BaseModel):
    email: str = Field(min_length=5, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=2, max_length=120)
    role: Literal["doctor", "nurse", "admin"]


class UserPatch(BaseModel):
    role: Literal["doctor", "nurse", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    reset_password: bool = False


def _user_view(u: User, active_sessions: int = 0) -> dict:
    return {"id": str(u.id), "email": u.email, "display_name": u.display_name, "role": u.role.key,
            "status": u.status, "last_login_at": u.last_login_at, "created_at": u.created_at,
            "active_sessions": active_sessions, "is_synthetic": u.is_synthetic}


@router.get("/admin/users")
def users(principal: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)) -> dict:
    from ..models import AuthSession

    counts = dict(db.execute(select(AuthSession.user_id, func.count()).where(
        AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()).group_by(AuthSession.user_id)).all())
    return {"items": [_user_view(u, counts.get(u.id, 0)) for u in db.scalars(select(User).order_by(User.display_name))],
            "roles": [{"key": r.key, "label": r.label, "description": r.description,
                       "permissions": sorted(p.key for p in r.permissions)} for r in db.scalars(select(Role))]}


@router.post("/admin/users", status_code=201)
def create_user(body: UserIn, principal: Principal = Depends(require("admin.users")), db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, detail={"code": "email_exists"})
    temp = "-".join(secrets.token_urlsafe(4) for _ in range(4))
    role = db.scalar(select(Role).where(Role.key == body.role))
    u = User(email=body.email.lower(), display_name=body.display_name, role_id=role.id,
             password_hash=passwords.hash_password(temp))
    db.add(u)
    db.flush()
    audit.record(db, action="admin.user_created", category="administration", outcome="success", resource_type="user",
                 resource_id=u.id, details={"role": body.role}, **principal.audit_ctx())
    db.commit()
    return {**_user_view(u), "temporary_password": temp,
            "note": "Shown once. Give it to the user through a secure channel."}


@router.patch("/admin/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, principal: Principal = Depends(require("admin.users")),
               db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, detail={"code": "not_found"})
    if u.id == principal.user_id and (body.role or body.status == "disabled"):
        raise HTTPException(409, detail={"code": "self_change", "message": "Ask another administrator to change your own access."})
    changes: dict = {}
    if body.role and body.role != u.role.key:
        u.role_id = db.scalar(select(Role.id).where(Role.key == body.role))
        changes["role"] = body.role
    if body.status and body.status != u.status:
        u.status = body.status
        changes["status"] = body.status
    temp = None
    if body.reset_password:
        temp = "-".join(secrets.token_urlsafe(4) for _ in range(4))
        u.password_hash, u.password_changed_at = passwords.hash_password(temp), utcnow()
        changes["password_reset"] = True
    if changes:
        u.session_epoch += 1  # every existing session of this user ends now
        changes["sessions_revoked"] = sessions.revoke_all_for_user(db, u.id, "admin_change")
    audit.record(db, action="admin.user_updated", category="administration", outcome="success", resource_type="user",
                 resource_id=u.id, details=changes, **principal.audit_ctx())
    db.commit()
    db.refresh(u)
    return {**_user_view(u), **({"temporary_password": temp} if temp else {})}


class CareTeamIn(BaseModel):
    patient_id: uuid.UUID
    user_id: uuid.UUID
    role: Literal["attending", "consulting", "nursing", "intake"]
    reason: str = Field(min_length=5, max_length=200)


@router.get("/admin/care-team/{patient_id}")
def care_team(patient_id: uuid.UUID, principal: Principal = Depends(require("admin.care_team")),
              db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(CareTeamAssignment).where(CareTeamAssignment.patient_id == patient_id)
                      .order_by(CareTeamAssignment.granted_at.desc())).all()
    names = dict(db.execute(select(User.id, User.display_name)).all())
    return {"items": [{"id": str(r.id), "user": names.get(r.user_id), "user_id": str(r.user_id), "role": r.role,
                       "reason": r.reason, "granted_at": r.granted_at, "revoked_at": r.revoked_at} for r in rows]}


@router.post("/admin/care-team", status_code=201)
def grant(body: CareTeamIn, principal: Principal = Depends(require("admin.care_team")), db: Session = Depends(get_db)) -> dict:
    target = db.get(User, body.user_id)
    if target is None or target.role.key == "admin":
        raise HTTPException(422, detail={"code": "clinician_required", "message": "Only clinicians join care teams."})
    row = CareTeamAssignment(patient_id=body.patient_id, user_id=body.user_id, role=body.role, reason=body.reason,
                             granted_by=principal.user_id)
    db.add(row)
    audit.record(db, action="care_team.granted", category="administration", outcome="success", patient_id=body.patient_id,
                 resource_type="care_team", resource_id=body.user_id, details={"role": body.role, "by": "admin"},
                 **principal.audit_ctx())
    db.commit()
    return {"id": str(row.id)}


@router.post("/admin/care-team/{assignment_id}/revoke")
def revoke(assignment_id: uuid.UUID, principal: Principal = Depends(require("admin.care_team")),
           db: Session = Depends(get_db)) -> dict:
    row = db.get(CareTeamAssignment, assignment_id)
    if row is None or row.revoked_at:
        raise HTTPException(404, detail={"code": "not_found"})
    row.revoked_at = utcnow()
    audit.record(db, action="care_team.revoked", category="administration", outcome="success", patient_id=row.patient_id,
                 resource_type="care_team", resource_id=row.user_id, **principal.audit_ctx())
    db.commit()
    return {"revoked": True}


# --------------------------------------------------------------------------- configuration & diagnostics

def load_runtime_settings() -> None:
    """Apply administrator model selections persisted in var/runtime-settings.json."""
    try:
        data = json.loads(RUNTIME_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    s = get_settings()
    for key in ("llm_model_clinical", "llm_model_light"):
        if isinstance(data.get(key), str):
            setattr(s, key, data[key])


@router.get("/admin/config")
def config(principal: Principal = Depends(require("admin.config"))) -> dict:
    s = get_settings()
    try:
        tags = http().get(f"{s.ollama_url}/api/tags", timeout=5).json().get("models", [])
    except Exception:  # noqa: BLE001
        tags = []
    from medibridge_agents.escalation import rules
    from medibridge_agents.tracing import ALLOWED_KEYS

    from ..providers.ollama import OllamaProvider

    models = []
    provider = OllamaProvider()
    for t in tags:
        try:
            info = provider.model_info(t["name"])
            models.append({"name": t["name"], "size_gb": round(t.get("size", 0) / 1e9, 1), "parameter_size": info.parameter_size,
                           "quantization": info.quantization, "capabilities": info.capabilities, "is_local": info.is_local,
                           "context_length": info.context_length})
        except Exception as exc:  # noqa: BLE001 - e.g. a cloud model is refused by policy
            models.append({"name": t["name"], "refused": type(exc).__name__})
    r = rules()
    return {
        "provider": {"name": s.llm_provider, "endpoint": s.ollama_url, "hosted_phi_allowed": s.allow_hosted_phi,
                     "hosted_approval_ref": bool(s.hosted_phi_approval_ref)},
        "models": {"clinical": s.llm_model_clinical, "light": s.llm_model_light, "embedding": s.embedding_model,
                   "asr": f"faster-whisper/{s.asr_model} ({s.asr_compute_type})", "installed": models},
        "tracing": {"langsmith_enabled": s.langsmith_tracing, "endpoint_configured": bool(s.langsmith_endpoint),
                    "allowed_metadata_keys": sorted(ALLOWED_KEYS), "content_sent": False},
        "sessions": {"idle_minutes": s.medibridge_session_idle_minutes, "absolute_minutes": s.medibridge_session_absolute_minutes},
        "escalation_rules": {"version": r["version"], "status": r["status"], "policy": r["policy"],
                             "rules": [{"id": x["id"], "level": x["level"], "label": x["label"]} for x in r["rules"]]},
        "feature_flags": {"hosted_llm_providers": False, "mcp_write_tools": False, "external_tracing": bool(s.langsmith_tracing and s.langsmith_endpoint),
                          "automatic_speaker_diarization": False, "ai_prescribing": False},
        "guardrails": [
            {"id": 1, "name": "Verified identity", "enforced_by": "encounter verify-identity (two identifiers) + identity agent block"},
            {"id": 2, "name": "Authorization & patient scope", "enforced_by": "RBAC + care-team checks on every route and tool"},
            {"id": 3, "name": "Handoff privacy", "enforced_by": "allow-list serializers + deterministic identifier scanner"},
            {"id": 4, "name": "Hallucination prevention", "enforced_by": "evidence-id + number-fidelity validators"},
            {"id": 5, "name": "Medication safety", "enforced_by": "deterministic graph screen with cited label evidence"},
            {"id": 6, "name": "No autonomous prescribing", "enforced_by": "doctor-only DB triggers; no model-to-prescription path"},
            {"id": 7, "name": "Human approval", "enforced_by": "three LangGraph gates; approvals bound to content hashes"},
            {"id": 8, "name": "Prompt injection", "enforced_by": "untrusted-data framing, read-only model tools, id checks"},
            {"id": 9, "name": "Trace protection", "enforced_by": "tracing off by default; metadata allow-list only"},
            {"id": 10, "name": "Clinical uncertainty", "enforced_by": "negation/certainty validators; conflicts surfaced"},
            {"id": 11, "name": "Escalation", "enforced_by": "configurable rules; clinician-only acknowledgement"},
            {"id": 12, "name": "Auditability", "enforced_by": "append-only hash-chained audit log"}],
    }


class ModelsIn(BaseModel):
    clinical: str = Field(min_length=2, max_length=80)
    light: str = Field(min_length=2, max_length=80)


@router.put("/admin/config/models")
def set_models(body: ModelsIn, principal: Principal = Depends(require("admin.config"))) -> dict:
    from ..providers.base import ProviderError
    from ..providers.ollama import OllamaProvider

    provider = OllamaProvider()
    for name in (body.clinical, body.light):
        try:
            info = provider.model_info(name)
        except ProviderError as exc:
            raise HTTPException(422, detail={"code": exc.code, "message": str(exc)})
        if "completion" not in info.capabilities:
            raise HTTPException(422, detail={"code": "not_a_chat_model", "message": f"{name} cannot generate text."})
    s = get_settings()
    before = {"clinical": s.llm_model_clinical, "light": s.llm_model_light}
    s.llm_model_clinical, s.llm_model_light = body.clinical, body.light
    RUNTIME_SETTINGS.parent.mkdir(exist_ok=True)
    RUNTIME_SETTINGS.write_text(json.dumps({"llm_model_clinical": body.clinical, "llm_model_light": body.light}), encoding="utf-8")
    audit.record_now(action="admin.models_changed", category="administration", outcome="success", resource_type="config",
                     details={"before": before, "after": body.model_dump()}, **principal.audit_ctx())
    return {"clinical": body.clinical, "light": body.light}


@router.post("/admin/diagnostics")
def diagnostics(principal: Principal = Depends(require("admin.diagnostics")), db: Session = Depends(get_db)) -> dict:
    from ..health import run_health
    from ..services import vector_index

    started = datetime.now()
    outbox = dict(db.execute(select(OutboxItem.status, func.count()).group_by(OutboxItem.status)).all())
    ingestion = dict(db.execute(select(DocumentVersion.ingestion_status, func.count())
                                .group_by(DocumentVersion.ingestion_status)).all())
    try:
        index = vector_index.stats()
    except Exception as exc:  # noqa: BLE001
        index = {"error": type(exc).__name__}
    result = {"health": run_health(), "audit_chain": audit.verify_chain(db), "outbox": outbox,
              "document_ingestion": ingestion, "vector_index": index,
              "duration_ms": int((datetime.now() - started).total_seconds() * 1000)}
    audit.record(db, action="admin.diagnostics", category="administration", outcome="success", resource_type="system",
                 details={"health": result["health"]["status"], "audit_intact": result["audit_chain"]["intact"]},
                 **principal.audit_ctx())
    db.commit()
    return result
