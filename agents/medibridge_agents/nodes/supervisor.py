"""Care Orchestrator (supervisor): plans the run, enforces dependencies and gates, routes failures, finalizes status.

Routing policy:
- critical agents (registry, scribe, chart, handoff brief, order entry, chart commit) stop the run when they fail
  or are blocked; the run ends visibly as failed/blocked;
- non-critical agents (evidence retrieval, the intake medication screen, the after-visit summary) degrade: their
  result is recorded as "unavailable" and the brief states what was not checked;
- no clinical write happens without the doctor's approvals; orders are screened before the commit, and blocking
  alerts go back to the doctor once (override with a reason, or revise).
"""
from __future__ import annotations

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import Encounter, WorkflowRun
from medibridge_api.models.base import utcnow

from ..runtime import AGENTS, RunContext, agent_node

PLAN = ["registry_check", "scribe_transcribe", "transcript_review", "scribe_extract", "chart_review",
        "evidence_retrieval", "medication_screen", "handoff_brief", "handoff_review", "order_draft", "orders_review",
        "order_safety_check", "safety_review", "chart_commit", "after_visit_summary"]
CRITICAL = {"registry_check", "scribe_transcribe", "transcript_review", "scribe_extract", "chart_review",
            "handoff_brief", "handoff_review", "order_draft", "orders_review", "order_safety_check", "safety_review",
            "chart_commit"}


@agent_node("supervisor", "supervisor_start", retries=0)
def supervisor_start(state: dict, ctx: RunContext) -> dict:
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.status, run.started_at = "running", run.started_at or utcnow()
        ctx.emit(db, "run.started", agent_id="supervisor", node="supervisor_start", status="running",
                 plan=PLAN, agents=list(AGENTS))
        db.commit()
    ctx.metrics.update({"plan_steps": len(PLAN)})
    return {"outcome": {}}


def stopped(state: dict) -> bool:
    return (state.get("outcome") or {}).get("status") in ("failed", "blocked")


def finalize(state: dict) -> dict:
    """Close the run with a status derived from what actually happened."""
    ctx = RunContext.from_state(state)
    outcome = state.get("outcome") or {}
    review = state.get("review") or {}
    doc = state.get("documentation") or {}
    if outcome.get("status") in ("failed", "blocked"):
        status, event = outcome["status"], f"run.{outcome['status']}"
    elif doc.get("status") == "committed":
        status, event = "completed", "run.completed"
    elif review.get("action") == "reject":
        status, event = "completed", "run.completed"
    elif (doc.get("submission") or {}).get("action") == "cancel":
        status, event = "cancelled", "run.cancelled"
    else:
        status, event = "completed", "run.completed"
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.status, run.ended_at, run.waiting_gate = status, utcnow(), None
        run.current_node = outcome.get("node") or "finalize"
        run.error_code = outcome.get("code")
        run.error_summary = (outcome.get("message") or None) if status == "blocked" else None
        enc = db.get(Encounter, ctx.encounter_id)
        if review.get("action") == "reject" and enc.status == "awaiting_review":
            enc.status, enc.version = "in_progress", enc.version + 1  # back to the clinicians; nothing was written
        ctx.emit(db, event, agent_id="supervisor", node="finalize", status=status,
                 **({"error_code": outcome["code"]} if outcome.get("code") else {}),
                 outcome="rejected" if review.get("action") == "reject" else
                         ("documented" if doc.get("status") == "committed" else status))
        db.commit()
    return {"outcome": {**outcome, "final_status": status}}
