"""Bounded tool layer (the MCP tool surface; also used in-process by the LangGraph agents).

Every call is authorized server-side, every time:
1. the run's actor must hold the tool's permission (RBAC) and care-team scope for the run's patient;
2. identifiers in arguments (patient_id / encounter_id) must equal the run's own ids; LLM-supplied ids are never
   trusted, and a mismatch is denied and audited as a security event;
3. write tools only accept callers on their allow-list. No LLM tool-calling path is on any write allow-list;
4. arguments are validated against the tool's schema; results never include contact or administrative identifiers.
Each invocation is recorded as a ``tool`` execution (name, timing, status) without arguments or results.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import CareTeamAssignment, HandoffReport, HandoffVersion, MedicalDocument, User
from medibridge_api.security import rbac
from medibridge_api.services import audit, knowledge, retrieval

from . import clinical_data
from .runtime import RunContext, finish_execution, start_execution


class ToolDenied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NoArgs(BaseModel):
    model_config = {"extra": "forbid"}


class ScopedIds(BaseModel):
    """Optional ids a caller may echo; they are compared with the run context, never used to select data."""
    model_config = {"extra": "forbid"}
    patient_id: str | None = None
    encounter_id: str | None = None


class SearchArgs(ScopedIds):
    query: str = Field(min_length=2, max_length=200)
    document_types: list[str] | None = None
    limit: int = Field(6, ge=1, le=10)


class DocumentArgs(ScopedIds):
    document_id: str


class DrugArgs(ScopedIds):
    name: str = Field(min_length=2, max_length=120)


class InteractionArgs(ScopedIds):
    medications: list[str] = Field(min_length=1, max_length=20)


class CommitArgs(ScopedIds):
    changeset_id: str
    expected_sha256: str = Field(min_length=64, max_length=64)


@dataclass
class ToolDef:
    name: str
    description: str
    kind: str  # read | write
    permission: str
    args: type[BaseModel]
    handler: Callable[[RunContext, BaseModel], Any]
    callers: set[str] | None = None  # write tools: explicit allow-list of callers
    llm_callable: bool = False  # may be offered to a model for tool calling
    tags: list[str] = field(default_factory=list)


TOOLS: dict[str, ToolDef] = {}


def tool(name: str, description: str, *, kind: str, permission: str, args: type[BaseModel] = NoArgs,
         callers: set[str] | None = None, llm_callable: bool = False):
    def register(fn):
        TOOLS[name] = ToolDef(name, description, kind, permission, args, fn, callers, llm_callable)
        return fn
    return register


def _scope_ok(actor_id: uuid.UUID, patient_id: uuid.UUID) -> bool:
    with get_sessionmaker()() as db:
        return db.scalar(select(CareTeamAssignment.id).where(
            CareTeamAssignment.user_id == actor_id, CareTeamAssignment.patient_id == patient_id,
            CareTeamAssignment.revoked_at.is_(None)).limit(1)) is not None


def _deny(ctx: RunContext, name: str, caller: str, code: str, message: str, category: str = "access") -> None:
    audit.record_now(action="tool.denied", category=category, outcome="denied", actor_user_id=ctx.actor_user_id,
                     actor_role=ctx.actor_role, patient_id=ctx.patient_id, encounter_id=ctx.encounter_id,
                     resource_type="tool", resource_id=name, details={"reason": code, "caller": caller})
    raise ToolDenied(code, message)


def invoke(ctx: RunContext, name: str, arguments: dict | None, *, caller: str) -> Any:
    td = TOOLS.get(name)
    if td is None:
        _deny(ctx, name, caller, "unknown_tool", f"Unknown tool {name}", "security")
    if caller.startswith("llm:") and not td.llm_callable:
        _deny(ctx, name, caller, "not_llm_callable", f"{name} cannot be called by a model", "security")
    if td.kind == "write" and caller not in (td.callers or set()):
        _deny(ctx, name, caller, "write_not_permitted", f"{caller} may not call write tool {name}", "security")
    if not rbac.role_has(ctx.actor_role, td.permission):
        _deny(ctx, name, caller, "permission_missing", f"Role lacks {td.permission}")
    if rbac.is_patient_scoped(td.permission) and not _scope_ok(ctx.actor_user_id, ctx.patient_id):
        _deny(ctx, name, caller, "no_care_team_assignment", "Actor is not in the patient's care team")
    try:
        args = td.args.model_validate(arguments or {})
    except ValidationError:
        _deny(ctx, name, caller, "invalid_arguments", f"Invalid arguments for {name}", "policy")
    for key, own in (("patient_id", ctx.patient_id), ("encounter_id", ctx.encounter_id)):
        supplied = getattr(args, key, None)
        if supplied is not None and supplied != str(own):
            _deny(ctx, name, caller, f"{key}_mismatch", f"{key} does not match the authenticated workflow context",
                  "security")
    started = time.perf_counter()
    ex_id = start_execution(ctx, caller.split(":")[-1], name, "tool", 1, tool_name=name, parent_id=ctx.execution_id)
    try:
        result = td.handler(ctx, args)
    except Exception as exc:  # noqa: BLE001
        finish_execution(ctx, ex_id, "failed", started=started, error_class=type(exc).__name__,
                         error_code=getattr(exc, "code", "tool_error"), kind="tool")
        raise
    finish_execution(ctx, ex_id, "succeeded", started=started, kind="tool",
                     metrics={"items": len(result) if isinstance(result, (list, dict)) else 1})
    return result


def specs(llm_only: bool = True) -> list[dict]:
    return [{"name": t.name, "description": t.description, "kind": t.kind, "permission": t.permission,
             "llm_callable": t.llm_callable, "parameters": t.args.model_json_schema()}
            for t in TOOLS.values() if t.llm_callable or not llm_only]


# ---------------------------------------------------------------------------- read tools

@tool("patient.lookup", "Minimal header of the patient bound to this workflow (no contact/admin identifiers).",
      kind="read", permission="patient.read_clinical")
def _patient_lookup(ctx: RunContext, args: NoArgs) -> dict:
    with get_sessionmaker()() as db:
        return clinical_data.patient_header(db, ctx.patient_id)


@tool("patient.get_history", "Prior encounters, conditions, medications, allergies, prescriptions and notes.",
      kind="read", permission="patient.read_clinical", args=ScopedIds)
def _history(ctx: RunContext, args: ScopedIds) -> dict:
    with get_sessionmaker()() as db:
        return clinical_data.history(db, ctx.patient_id, ctx.encounter_id)


@tool("encounter.get", "Current encounter header.", kind="read", permission="encounter.read", args=ScopedIds)
def _encounter(ctx: RunContext, args: ScopedIds) -> dict:
    with get_sessionmaker()() as db:
        return clinical_data.encounter_header(db, ctx.encounter_id)


@tool("medication.get_current", "Recorded medications classified current / historical / unknown.",
      kind="read", permission="medication.read", args=ScopedIds)
def _meds(ctx: RunContext, args: ScopedIds) -> list:
    with get_sessionmaker()() as db:
        return clinical_data.history(db, ctx.patient_id, ctx.encounter_id)["medications"]


@tool("allergy.get", "Recorded allergies with verification status.", kind="read", permission="medication.read",
      args=ScopedIds)
def _allergies(ctx: RunContext, args: ScopedIds) -> list:
    with get_sessionmaker()() as db:
        return clinical_data.history(db, ctx.patient_id, ctx.encounter_id)["allergies"]


@tool("document.search", "Hybrid search (BM25 + dense, fused and re-ranked) of this patient's medical documents. "
      "Returns cited, redacted passages.",
      kind="read", permission="document.read", args=SearchArgs, llm_callable=True)
def _doc_search(ctx: RunContext, args: SearchArgs) -> dict:
    with get_sessionmaker()() as db:
        res = retrieval.retrieve(db, patient_id=ctx.patient_id, query=args.query, limit=args.limit,
                                 document_types=args.document_types)
        return {"evidence": [e.as_dict() for e in res.evidence], "coverage": res.coverage, "dropped": res.dropped,
                "timings_ms": res.timings_ms, "mode": res.mode}


@tool("document.get_authorized", "Metadata of one document belonging to this patient.", kind="read",
      permission="document.read", args=DocumentArgs)
def _doc_get(ctx: RunContext, args: DocumentArgs) -> dict:
    with get_sessionmaker()() as db:
        try:
            doc = db.get(MedicalDocument, uuid.UUID(args.document_id))
        except ValueError:
            doc = None
        if doc is None or doc.patient_id != ctx.patient_id:
            _deny(ctx, "document.get_authorized", "tool", "document_not_in_scope", "Document not found for this patient",
                  "security")
        return {"document_id": str(doc.id), "title": doc.title, "document_type": doc.document_type,
                "clinical_date": doc.clinical_date.isoformat() if doc.clinical_date else None,
                "source_name": doc.source_name, "current_version": doc.current_version}


@tool("graph.lookup_drug", "Resolve a medication name against the knowledge graph (reference data).", kind="read",
      permission="medication.read", args=DrugArgs, llm_callable=True)
def _drug(ctx: RunContext, args: DrugArgs) -> dict:
    return knowledge.safe_graph().lookup_drug(args.name)


@tool("graph.check_interactions", "Deterministic interaction screen for a list of medication names.", kind="read",
      permission="medication.read", args=InteractionArgs)
def _interactions(ctx: RunContext, args: InteractionArgs) -> dict:
    return knowledge.safe_graph().screen([{"text": m, "source_kind": "unconfirmed"} for m in args.medications], [], [])


@tool("handoff.get", "Latest handoff version for this encounter.", kind="read", permission="handoff.read", args=ScopedIds)
def _handoff_get(ctx: RunContext, args: ScopedIds) -> dict | None:
    with get_sessionmaker()() as db:
        report = db.scalars(select(HandoffReport).where(HandoffReport.encounter_id == ctx.encounter_id)
                            .order_by(HandoffReport.created_at.desc())).first()
        if not report:
            return None
        v = db.scalars(select(HandoffVersion).where(HandoffVersion.handoff_id == report.id,
                                                    HandoffVersion.version == report.current_version)).first()
        return {"handoff_id": str(report.id), "status": report.status, "version": report.current_version,
                "content_sha256": v.content_sha256 if v else None, "content": v.content if v else None}


class ScreenArgs(ScopedIds):
    medications: list[dict] = Field(default_factory=list, max_length=60)
    allergies: list[dict] = Field(default_factory=list, max_length=40)
    conditions: list[dict] = Field(default_factory=list, max_length=60)
    context: dict = Field(default_factory=dict)


@tool("graph.screen_patient", "Deterministic interaction/allergy/condition screen with completeness reporting.",
      kind="read", permission="medication.read", args=ScreenArgs)
def _screen(ctx: RunContext, args: ScreenArgs) -> dict:
    return knowledge.safe_graph().screen(args.medications, args.allergies, args.conditions, args.context)


# ---------------------------------------------------------------------------- write tools (never LLM-callable)

class HandoffDraftArgs(ScopedIds):
    content: dict
    evidence_bundle: dict
    model: str | None = None
    prompt_version: str | None = None
    validation: dict = Field(default_factory=dict)
    change_summary: str = Field("", max_length=500)


@tool("handoff.create_draft", "Persist an AI handoff draft as a new immutable version awaiting doctor review.",
      kind="write", permission="handoff.read", args=HandoffDraftArgs, callers={"agent:handoff_brief"})
def _handoff_create(ctx: RunContext, args: HandoffDraftArgs) -> dict:
    from . import handoff_store

    with get_sessionmaker()() as db:
        v = handoff_store.create_version(db, encounter_id=ctx.encounter_id, patient_id=ctx.patient_id,
                                         run_id=ctx.run_id, content=args.content, evidence_bundle=args.evidence_bundle,
                                         author_kind="ai", author_user_id=None, model=args.model,
                                         prompt_version=args.prompt_version, validation=args.validation,
                                         change_summary=args.change_summary, status="in_review")
        audit.record(db, action="handoff.drafted", category="workflow", outcome="success", patient_id=ctx.patient_id,
                     encounter_id=ctx.encounter_id, resource_type="handoff_version", resource_id=v.id,
                     actor_user_id=None, actor_role="system:handoff_brief_agent", details={"version": v.version})
        db.commit()
        return {"handoff_id": str(v.handoff_id), "version": v.version, "content_sha256": v.content_sha256,
                "version_id": str(v.id)}


@tool("handoff.submit_for_review", "Mark the current handoff draft as awaiting doctor review.", kind="write",
      permission="handoff.read", args=ScopedIds, callers={"agent:handoff_brief"})
def _handoff_submit(ctx: RunContext, args: ScopedIds) -> dict:
    from . import handoff_store

    with get_sessionmaker()() as db:
        report = handoff_store.get_report(db, ctx.encounter_id)
        if report is None:
            raise ToolDenied("no_handoff", "No handoff exists for this encounter")
        if report.status in ("draft", "changes_requested"):
            report.status = "in_review"
        db.commit()
        return {"status": report.status, "version": report.current_version}


class PrepareArgs(ScopedIds):
    documentation: dict


@tool("documentation.prepare_update", "Validate and stage (not commit) a doctor's documentation change set.",
      kind="write", permission="note.write", args=PrepareArgs, callers={"api:doctor"})
def _doc_prepare(ctx: RunContext, args: PrepareArgs) -> dict:
    from . import documentation_store as ds

    if not actor_is_doctor(ctx.actor_user_id):
        raise ToolDenied("doctor_required", "Only a doctor can prepare clinical documentation")
    with get_sessionmaker()() as db:
        cs = ds.prepare(db, encounter_id=ctx.encounter_id, run_id=ctx.run_id, doctor_id=ctx.actor_user_id,
                        data=ds.DocumentationIn.model_validate(args.documentation))
        db.commit()
        return {"changeset_id": str(cs.id), "content_sha256": cs.content_sha256, "content": cs.content}


@tool("documentation.commit_approved", "Commit a prepared change set; requires the doctor's matching hash.",
      kind="write", permission="note.approve", args=CommitArgs, callers={"agent:chart"})
def _doc_commit(ctx: RunContext, args: CommitArgs) -> dict:
    from . import documentation_store as ds

    if not actor_is_doctor(ctx.actor_user_id):
        raise ToolDenied("doctor_required", "Only a doctor can commit clinical documentation")
    with get_sessionmaker()() as db:
        try:
            result = ds.commit(db, changeset_id=uuid.UUID(args.changeset_id), expected_sha256=args.expected_sha256,
                               doctor_id=ctx.actor_user_id, run_id=ctx.run_id,
                               audit_ctx={"actor_user_id": ctx.actor_user_id, "actor_role": ctx.actor_role})
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result


def actor_is_doctor(user_id: uuid.UUID) -> bool:
    with get_sessionmaker()() as db:
        u = db.get(User, user_id)
        return bool(u and u.status == "active" and u.role.key == "doctor")
