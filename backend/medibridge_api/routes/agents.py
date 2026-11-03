"""Agent Mission Control and Observability. Execution metadata only: ids, timings, statuses, counts, models."""
from __future__ import annotations

from datetime import timedelta
from statistics import median

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AgentExecution, AuditLog, CareTeamAssignment, WorkflowEvent, WorkflowRun
from ..models.base import utcnow
from ..security.deps import Principal, require

router = APIRouter(prefix="/api", tags=["agents"])


def _scope(stmt, principal: Principal, column):
    if principal.role == "admin":
        return stmt
    care = select(CareTeamAssignment.patient_id).where(CareTeamAssignment.user_id == principal.user_id,
                                                       CareTeamAssignment.revoked_at.is_(None))
    return stmt.where(column.in_(care))


@router.get("/agents/topology")
def topology(principal: Principal = Depends(require("agents.monitor"))) -> dict:
    from medibridge_agents.graph import topology as graph_topology
    from medibridge_agents.runtime import AGENTS
    from medibridge_agents.tools import specs

    from ..config import get_settings

    return {"graph": graph_topology(), "agents": AGENTS, "tools": specs(llm_only=False),
            "transport": get_settings().agent_transport}


@router.get("/agents/a2a")
def a2a_status(principal: Principal = Depends(require("agents.monitor"))) -> dict:
    """Each agent service's endpoint and whether it is serving its agent card right now."""
    from medibridge_agents.a2a.status import probe_all

    return probe_all()


@router.get("/agents/runs")
def runs(limit: int = Query(30, ge=1, le=200), status: str | None = None,
         principal: Principal = Depends(require("agents.monitor")), db: Session = Depends(get_db)) -> dict:
    stmt = _scope(select(WorkflowRun), principal, WorkflowRun.patient_id).order_by(WorkflowRun.created_at.desc())
    if status:
        stmt = stmt.where(WorkflowRun.status.in_(status.split(",")))
    rows = db.scalars(stmt.limit(limit)).all()
    ex_counts = dict(db.execute(select(AgentExecution.run_id, func.count()).where(
        AgentExecution.run_id.in_([r.id for r in rows])).group_by(AgentExecution.run_id)).all()) if rows else {}
    return {"items": [{"id": str(r.id), "encounter_id": str(r.encounter_id), "status": r.status,
                       "current_node": r.current_node, "waiting_gate": r.waiting_gate, "error_code": r.error_code,
                       "created_at": r.created_at, "started_at": r.started_at, "ended_at": r.ended_at,
                       "executions": ex_counts.get(r.id, 0)} for r in rows]}


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


@router.get("/observability/summary")
def summary(hours: int = Query(24 * 7, ge=1, le=24 * 90), principal: Principal = Depends(require("observability.read")),
            db: Session = Depends(get_db)) -> dict:
    since = utcnow() - timedelta(hours=hours)
    stmt = (select(AgentExecution).join(WorkflowRun, WorkflowRun.id == AgentExecution.run_id)
            .where(AgentExecution.started_at >= since))
    rows = db.scalars(_scope(stmt, principal, WorkflowRun.patient_id)).all()
    by_agent: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    a2a: dict[str, dict] = {}  # supervisor -> agent calls, keyed "agent/skill"
    llm_calls, retrieval_ms, timeline = [], [], []
    for x in rows:
        bucket = {"tool": tools, "a2a": a2a}.get(x.kind, by_agent)
        key = x.tool_name if x.kind in ("tool", "a2a") else x.agent_id
        b = bucket.setdefault(key, {"runs": 0, "failed": 0, "blocked": 0, "durations": [], "prompt_tokens": 0,
                                    "completion_tokens": 0, "retries": 0})
        b["runs"] += 1
        b["failed"] += x.status in ("failed", "timed_out")
        b["blocked"] += x.status == "blocked"
        b["retries"] += x.attempt > 1
        if x.kind == "a2a" and x.error_code:
            b.setdefault("errors", {})
            b["errors"][x.error_code] = b["errors"].get(x.error_code, 0) + 1
        if x.duration_ms is not None and x.status != "skipped":
            b["durations"].append(x.duration_ms)
        b["prompt_tokens"] += x.prompt_tokens or 0
        b["completion_tokens"] += x.completion_tokens or 0
        if x.model and x.duration_ms:
            llm_calls.append({"agent": x.agent_id, "model": x.model, "ms": x.duration_ms, "load_ms": x.load_ms,
                              "prompt_tokens": x.prompt_tokens, "completion_tokens": x.completion_tokens})
        if x.kind == "tool" and x.tool_name == "document.search" and x.duration_ms:
            retrieval_ms.append(x.duration_ms)
        if x.kind == "agent" and x.duration_ms is not None:
            timeline.append({"t": x.started_at, "agent": x.agent_id, "ms": x.duration_ms, "status": x.status})

    def fold(d: dict) -> list[dict]:
        out = []
        for k, b in sorted(d.items()):
            ds = b.pop("durations")
            out.append({"name": k, **b, "p50_ms": int(median(ds)) if ds else None, "p95_ms": _pct(ds, 0.95),
                        "max_ms": max(ds) if ds else None})
        return out

    run_stmt = _scope(select(WorkflowRun.status, func.count()).where(WorkflowRun.created_at >= since),
                      principal, WorkflowRun.patient_id).group_by(WorkflowRun.status)
    ev_stmt = _scope(select(WorkflowEvent.type, func.count()).where(
        WorkflowEvent.created_at >= since,
        WorkflowEvent.type.in_(("node.blocked", "escalation.raised", "escalation.acknowledged", "run.failed",
                                "run.blocked", "run.interrupted", "gate.waiting", "gate.resolved"))),
        principal, WorkflowEvent.patient_id).group_by(WorkflowEvent.type)
    denials = dict(db.execute(select(AuditLog.action, func.count()).where(
        AuditLog.occurred_at >= since, AuditLog.outcome == "denied").group_by(AuditLog.action)).all()) \
        if principal.role == "admin" else {}
    from ..config import get_settings
    s = get_settings()
    return {
        "window_hours": hours,
        "agents": fold(by_agent), "tools": fold(tools), "a2a": fold(a2a), "transport": s.agent_transport,
        "llm": {"calls": len(llm_calls), "p50_ms": int(median([c["ms"] for c in llm_calls])) if llm_calls else None,
                "p95_ms": _pct([c["ms"] for c in llm_calls], 0.95),
                "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in llm_calls),
                "completion_tokens": sum(c["completion_tokens"] or 0 for c in llm_calls),
                "by_model": {m: sum(1 for c in llm_calls if c["model"] == m) for m in {c["model"] for c in llm_calls}},
                "recent": llm_calls[-40:]},
        "retrieval": {"calls": len(retrieval_ms), "p50_ms": int(median(retrieval_ms)) if retrieval_ms else None,
                      "p95_ms": _pct(retrieval_ms, 0.95)},
        "runs_by_status": dict(db.execute(run_stmt).all()),
        "guardrail_events": dict(db.execute(ev_stmt).all()),
        "access_denials": denials,
        "timeline": sorted(timeline, key=lambda t: t["t"])[-300:],
        "models": {"clinical": s.llm_model_clinical, "light": s.llm_model_light, "embedding": s.embedding_model,
                   "asr": s.asr_model, "provider": s.llm_provider},
        "tracing": {"langsmith_enabled": s.langsmith_tracing, "endpoint_configured": bool(s.langsmith_endpoint)},
    }
