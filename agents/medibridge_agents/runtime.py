"""Agent runtime: run context, execution telemetry and the node wrapper used by every agent.

Telemetry rows (agent_executions, workflow_events) carry ids, timings, counts, model names and reason codes,
never transcript, document or note text. The UI's live progress is derived only from these rows.
"""
from __future__ import annotations

import functools
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langgraph.errors import GraphBubbleUp
from sqlalchemy.orm import Session

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import AgentExecution, WorkflowRun
from medibridge_api.models.base import utcnow
from medibridge_api.providers.base import ProviderUnavailable, StructuredOutputError, Usage
from medibridge_api.services import events

from . import tracing

MAX_STEPS = 40
log = logging.getLogger("medibridge.agents")


class NodeBlocked(Exception):
    """A guardrail stopped the workflow. ``code`` is shown to users and recorded (no PHI)."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class Transient(Exception):
    """A failure worth retrying (e.g. model temporarily unavailable)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


AGENTS: dict[str, dict] = {
    "supervisor": {"label": "Care Orchestrator", "kind": "orchestrator",
                   "role": "Plans the run, enforces dependencies and the four human gates, routes failures."},
    "patient_registry": {"label": "Patient Registry (EMPI)", "kind": "deterministic",
                         "role": "Finds a returning patient by MRN and date of birth or registers a new one; checks identity and consent."},
    "ambient_scribe": {"label": "Ambient Scribe", "kind": "asr+llm",
                       "role": "Transcribes the intake locally and extracts symptoms, medicines, allergies and conditions."},
    "chart": {"label": "Chart Agent (EHR)", "kind": "deterministic",
              "role": "Reads the structured record from Postgres and writes the approved encounter back with its audit trail."},
    "evidence_retrieval": {"label": "Evidence Retrieval", "kind": "hybrid retrieval",
                           "role": "BM25 + Qdrant dense search, reciprocal rank fusion and re-ranking over this patient's reports."},
    "medication_safety": {"label": "Medication Safety CDS", "kind": "deterministic",
                          "role": "Screens current and proposed medicines (with doses) against the Neo4j drug knowledge graph."},
    "handoff_brief": {"label": "Clinical Handoff Brief", "kind": "llm",
                      "role": "Writes the structured, cited cardiology brief the doctor reviews."},
    "order_entry": {"label": "Order Entry (CPOE)", "kind": "llm+deterministic",
                    "role": "Drafts the note and verifies the doctor's orders with Medication Safety before anything is saved."},
    "after_visit_summary": {"label": "After-Visit Summary", "kind": "llm+deterministic",
                            "role": "Writes the patient's plain-language summary of this visit: reason, medicines and how to take them."},
}


@dataclass
class RunContext:
    run_id: uuid.UUID
    encounter_id: uuid.UUID
    patient_id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_role: str
    metrics: dict = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    execution_id: uuid.UUID | None = None
    parent_execution_id: uuid.UUID | None = None  # the supervisor's a2a call, when run as a remote agent

    @classmethod
    def from_state(cls, state: dict) -> "RunContext":
        parent = state.get("parent_execution_id")
        return cls(uuid.UUID(state["run_id"]), uuid.UUID(state["encounter_id"]), uuid.UUID(state["patient_id"]),
                   uuid.UUID(state["actor_user_id"]), state["actor_role"],
                   parent_execution_id=uuid.UUID(parent) if parent else None)

    def emit(self, db: Session, type: str, **kw) -> None:
        events.emit(db, type, run_id=self.run_id, encounter_id=self.encounter_id, patient_id=self.patient_id, **kw)

    def add_usage(self, usage: Usage | None) -> None:
        if usage:
            self.usage.add(usage)


def _set_run(db: Session, run_id: uuid.UUID, **values) -> None:
    run = db.get(WorkflowRun, run_id)
    for k, v in values.items():
        setattr(run, k, v)


def start_execution(ctx: RunContext, agent_id: str, node: str, kind: str, attempt: int,
                    tool_name: str | None = None, parent_id: uuid.UUID | None = None) -> uuid.UUID:
    with get_sessionmaker()() as db:
        ex = AgentExecution(id=uuid.uuid4(), run_id=ctx.run_id, parent_id=parent_id, kind=kind, agent_id=agent_id,
                            node=node, tool_name=tool_name, attempt=attempt, status="running")
        db.add(ex)
        if kind in ("agent", "gate"):
            _set_run(db, ctx.run_id, current_node=node, status="running")
        ctx.emit(db, {"tool": "tool.started", "a2a": "a2a.request"}.get(kind, "node.started"), agent_id=agent_id, node=node,
                 tool_name=tool_name, status="running", attempt=attempt, execution_id=str(ex.id))
        db.commit()
        return ex.id


def finish_execution(ctx: RunContext, execution_id: uuid.UUID, status: str, *, started: float,
                     error_code: str | None = None, error_class: str | None = None, usage: Usage | None = None,
                     metrics: dict | None = None, kind: str = "agent") -> None:
    with get_sessionmaker()() as db:
        ex = db.get(AgentExecution, execution_id)
        ex.status, ex.ended_at = status, utcnow()
        ex.duration_ms = int((time.perf_counter() - started) * 1000)
        ex.error_code, ex.error_class = error_code, error_class
        if usage and usage.calls:
            ex.model, ex.model_digest = usage.model, usage.model_digest
            ex.prompt_tokens, ex.completion_tokens, ex.load_ms = usage.prompt_tokens, usage.completion_tokens, usage.load_ms
        ex.metrics = metrics or {}
        event = {"succeeded": "completed", "failed": "failed", "blocked": "blocked", "timed_out": "failed",
                 "skipped": "skipped"}.get(status, status)
        name = "a2a.response" if kind == "a2a" else f"{'tool' if kind == 'tool' else 'node'}.{event}"
        ctx.emit(db, name, agent_id=ex.agent_id, node=ex.node, tool_name=ex.tool_name, status=status,
                 duration_ms=ex.duration_ms, execution_id=str(ex.id), **({"error_code": error_code} if error_code else {}))
        db.commit()


def agent_node(agent_id: str, node: str | None = None, *, retries: int = 1, budget_s: float = 600,
               degrade_key: str | None = None) -> Callable:
    """Wrap a node function ``fn(state, ctx) -> dict`` with telemetry, step budget, retries and error routing.

    ``degrade_key``: for non-critical agents, a failure is recorded under this state key as
    ``{"status": "unavailable", "reason": code}`` and the run continues instead of stopping.
    """
    node = node or agent_id

    def deco(fn: Callable[[dict, RunContext], dict]) -> Callable[[dict], dict]:
        def fail(steps: int, status: str, code: str, **extra) -> dict:
            if degrade_key:
                return {"steps": steps, degrade_key: {"status": "unavailable", "reason": code}}
            return {"steps": steps, "outcome": {"status": status, "node": node, "code": code, **extra}}

        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            steps = int(state.get("steps", 0)) + 1
            ctx = RunContext.from_state(state)
            if steps > MAX_STEPS:
                return {"steps": steps, "outcome": {"status": "failed", "node": node, "code": "step_budget_exceeded"}}
            attempt = 0
            while True:
                attempt += 1
                ctx.metrics, ctx.usage = {}, Usage()
                started = time.perf_counter()
                ex_id = start_execution(ctx, agent_id, node, "agent", attempt, parent_id=ctx.parent_execution_id)
                ctx.execution_id = ex_id
                try:
                    with tracing.trace(f"medibridge.{node}", {"run_id": str(ctx.run_id), "agent_id": agent_id,
                                                              "node": node, "attempt": attempt,
                                                              "execution_id": str(ex_id)}) as trace_out:
                        result = fn(state, ctx) or {}
                        trace_out.update({**ctx.metrics, "model": ctx.usage.model or None,
                                          "prompt_tokens": ctx.usage.prompt_tokens,
                                          "completion_tokens": ctx.usage.completion_tokens})
                    elapsed = time.perf_counter() - started
                    if elapsed > budget_s:
                        ctx.metrics["over_budget_s"] = round(elapsed - budget_s, 1)
                    finish_execution(ctx, ex_id, "succeeded", started=started, usage=ctx.usage, metrics=ctx.metrics)
                    return {**result, "steps": steps}
                except GraphBubbleUp:
                    # A gate interrupt: not a failure. The execution resumes when a person answers.
                    finish_execution(ctx, ex_id, "skipped", started=started, metrics={"paused_for": "human_gate"})
                    raise
                except NodeBlocked as b:
                    finish_execution(ctx, ex_id, "blocked", started=started, error_code=b.code, metrics=ctx.metrics)
                    return fail(steps, "blocked", b.code, message=b.message, **b.details)
                except (Transient, ProviderUnavailable) as exc:
                    code = getattr(exc, "code", "transient")
                    finish_execution(ctx, ex_id, "failed", started=started, error_code=code,
                                     error_class=type(exc).__name__, usage=ctx.usage)
                    if attempt <= retries:
                        time.sleep(min(2 ** attempt, 8))
                        continue
                    return fail(steps, "failed", code)
                except StructuredOutputError as exc:
                    finish_execution(ctx, ex_id, "failed", started=started, error_code=exc.code,
                                     error_class=type(exc).__name__, usage=exc.usage,
                                     metrics={"attempts": exc.attempts, "problems": len(exc.problems)})
                    return fail(steps, "failed", exc.code)
                except Exception as exc:  # noqa: BLE001 - unexpected bug: record class only (message may hold PHI)
                    # Full traceback only to the local server log (not telemetry, not traces).
                    log.exception("agent %s failed unexpectedly", node)
                    finish_execution(ctx, ex_id, "failed", started=started, error_code="unexpected_error",
                                     error_class=type(exc).__name__)
                    return fail(steps, "failed", "unexpected_error", error_class=type(exc).__name__)

        wrapper.agent_id = agent_id  # type: ignore[attr-defined]
        return wrapper

    return deco
