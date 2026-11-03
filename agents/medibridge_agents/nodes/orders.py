"""Orders loop: the doctor's orders are checked by Medication Safety before the Chart agent writes anything.

    order_draft -> [GATE orders_review] -> order_safety_check -(clear)-> chart_commit -> after_visit_summary
                                                                -(alerts)-> [GATE safety_review]
                                                                   -(override, with reason)-> chart_commit
                                                                   -(revise)-> orders_review (new orders, screened again)

- Order Entry (CPOE) verifies the signed change set and asks Medication Safety, agent-to-agent with a capability
  the supervisor delegated for exactly this call, to screen the proposed orders against the patient's current
  medicines and doses (Postgres), today's reported medicines, allergies and conditions.
- Blocking alerts (contraindicated / major) and orders that cannot be screened go back to the doctor once. An
  override with a reason goes straight to the commit; it is not re-screened. Revised orders are screened again.
"""
from __future__ import annotations

import uuid

from langgraph.types import interrupt
from sqlalchemy import select

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import (Allergy, Condition, DocumentationChangeSet, HandoffVersion, MedicationStatement,
                                   WorkflowRun)
from medibridge_api.services import knowledge

from .. import documentation_store as ds
from .. import tools
from ..a2a import peer
from ..runtime import NodeBlocked, RunContext, agent_node


def _gate(state: dict, gate: str, payload: dict) -> dict:
    ctx = RunContext.from_state(state)
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        if run.status != "waiting_human" or run.waiting_gate != gate:
            run.status, run.waiting_gate, run.current_node = "waiting_human", gate, gate
            ctx.emit(db, "gate.waiting", node=gate, agent_id="supervisor", status="waiting_human", gate=gate)
            db.commit()
    decision = interrupt({"gate": gate, **payload})
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.status, run.waiting_gate = "running", None
        ctx.emit(db, "gate.resolved", node=gate, agent_id="supervisor", status="running", gate=gate,
                 decision=decision.get("action"))
        db.commit()
    return decision


def orders_review_gate(state: dict) -> dict:
    """Gate 3: the doctor writes assessment, plan and orders, then signs the exact prepared change set."""
    doc = dict(state.get("documentation") or {})
    decision = _gate(state, "orders_review", {"previous_safety": doc.get("safety")})
    doc["submission"] = {"action": decision.get("action"), "changeset_id": decision.get("changeset_id"),
                         "expected_sha256": decision.get("expected_sha256")}
    doc.pop("safety", None)
    doc.pop("safety_decision", None)
    return {"documentation": doc, "actor_user_id": decision.get("actor_user_id", state["actor_user_id"]),
            "actor_role": decision.get("actor_role", state["actor_role"])}


def _submission(state: dict) -> tuple[uuid.UUID, str]:
    sub = (state.get("documentation") or {}).get("submission") or {}
    try:
        return uuid.UUID(str(sub.get("changeset_id"))), str(sub.get("expected_sha256"))
    except ValueError:
        raise NodeBlocked("no_changeset", "No signed change set was submitted.") from None


@agent_node("order_entry", "order_safety_check", retries=0)
def order_safety_check_node(state: dict, ctx: RunContext) -> dict:
    changeset_id, sha = _submission(state)
    with get_sessionmaker()() as db:
        cs = db.get(DocumentationChangeSet, changeset_id)
        if cs is None or cs.encounter_id != ctx.encounter_id or cs.status != "prepared" or cs.content_sha256 != sha:
            raise NodeBlocked("changeset_stale", "The signed orders are no longer the prepared ones; prepare them again.")
        n_orders = len(cs.content.get("prescriptions", []))
    if n_orders == 0:
        screen = {"status": "clear", "alerts": [], "blocking": 0, "statement": "No medication orders to screen."}
    else:
        try:
            result = peer.call("medication_safety", "screen_proposed_orders",
                               {k: state[k] for k in ("run_id", "encounter_id", "patient_id", "actor_user_id", "actor_role")}
                               | {"steps": state.get("steps", 0),
                                  "documentation": {"submission": {"changeset_id": str(changeset_id), "expected_sha256": sha}}})
            screen = result.get("order_screen") or {}
            if not screen:
                raise peer.PeerUnavailable((result.get("outcome") or {}).get("code") or "empty_screen")
        except peer.PeerUnavailable as exc:
            screen = {"status": "alerts", "blocking": 1, "statement": "The proposed orders could not be screened.",
                      "alerts": [{"kind": "screening_unavailable", "id": "screening-unavailable", "severity": "major",
                                  "summary": "Medication Safety could not screen these orders "
                                             f"({exc.code}). Revise, or override with a documented reason.",
                                  "drugs": [], "patient_facts": [], "evidence": [], "confidence": "established",
                                  "depends_on": []}]}
    with get_sessionmaker()() as db:
        ds.record_screen(db, changeset_id=changeset_id, expected_sha256=sha, screen=screen)
        db.commit()
    blocking = ds.blocking(screen)
    status = "alerts" if blocking else "clear"
    ctx.metrics.update({"orders": n_orders, "alerts": len(screen.get("alerts", [])), "blocking": len(blocking),
                        "status": status})
    doc = dict(state.get("documentation") or {})
    doc["safety"] = {"status": status, "blocking": len(blocking), "alerts": screen.get("alerts", []),
                     "statement": screen.get("statement"), "changeset_id": str(changeset_id), "content_sha256": sha}
    return {"documentation": doc}


@agent_node("medication_safety", "order_safety_check", retries=1)
def screen_proposed_orders(state: dict, ctx: RunContext) -> dict:
    """Peer skill (called by Order Entry): screen new orders against everything the patient takes and has."""
    changeset_id, sha = _submission(state)
    with get_sessionmaker()() as db:
        cs = db.get(DocumentationChangeSet, changeset_id)
        if cs is None or cs.patient_id != ctx.patient_id or cs.content_sha256 != sha:
            raise NodeBlocked("changeset_mismatch", "The change set does not belong to this patient and run.")
        bundle = db.get(HandoffVersion, cs.handoff_version_id).evidence_bundle
        current = [{"text": m.medication_text, "ref": f"medication_statement:{m.id}", "source_kind": m.source_kind,
                    "certainty": m.certainty, "dose_text": m.dose_text, "frequency_text": m.frequency_text}
                   for m in db.scalars(select(MedicationStatement).where(
                       MedicationStatement.patient_id == ctx.patient_id,
                       MedicationStatement.med_status.in_(("active", "on_hold", "intended", "unknown")),
                       MedicationStatement.status != "entered_in_error"))]
        allergies = [{"substance": a.substance_text, "ref": f"allergy:{a.id}", "verification_status": a.verification_status,
                      "source_kind": a.source_kind} for a in db.scalars(select(Allergy).where(
                          Allergy.patient_id == ctx.patient_id, Allergy.clinical_status == "active",
                          Allergy.verification_status.notin_(("refuted", "entered_in_error"))))]
        conditions = [{"name": c.name, "ref": f"condition:{c.id}", "clinical_status": c.clinical_status}
                      for c in db.scalars(select(Condition).where(Condition.patient_id == ctx.patient_id,
                                                                  Condition.status != "entered_in_error"))]
        orders = cs.content.get("prescriptions", [])
    reported = [{"text": v["label"], "ref": k, "source_kind": "patient_reported", "certainty": v.get("certainty", "stated"),
                 "dose_text": v.get("dose"), "frequency_text": v.get("frequency")}
                for k, v in bundle.items() if k.startswith("intake:medication")]
    allergies += [{"substance": v["label"], "ref": k, "verification_status": "unconfirmed", "source_kind": "patient_reported"}
                  for k, v in bundle.items() if k.startswith("intake:allergy")]
    new = [{"text": o["medication_text"], "ref": f"new_order:{i}", "source_kind": "clinician_documented",
            "dose_text": o.get("dose_text"), "frequency_text": o.get("frequency_text")} for i, o in enumerate(orders)]
    try:
        result = tools.invoke(ctx, "graph.screen_patient", {"medications": current + reported + new, "allergies": allergies,
                                                            "conditions": conditions, "context": {}},
                              caller="agent:medication_safety")
    except knowledge.KnowledgeUnavailable:
        raise NodeBlocked("knowledge_unavailable", "The drug knowledge graph is unavailable; orders cannot be screened.")
    alerts = [a for a in result["alerts"] if any(str(f.get("ref", "")).startswith("new_order:") for f in a["patient_facts"])]
    for m in result["medications"]:
        if str(m.get("ref", "")).startswith("new_order:") and m["resolution"]["match_type"] in ("unresolved", "suggestion"):
            alerts.append({"kind": "unscreenable_order", "id": f"unscreenable:{m['ref']}", "severity": "major",
                           "summary": f"'{m['text']}' could not be identified in the knowledge release, so it was not "
                                      "screened. Use the generic name, or override with a reason.",
                           "drugs": [], "patient_facts": [{"text": m["text"], "ref": m["ref"]}], "evidence": [],
                           "confidence": "established", "depends_on": []})
    blocking = ds.blocking({"alerts": alerts})
    ctx.metrics.update({"orders": len(orders), "alerts": len(alerts), "blocking": len(blocking)})
    return {"order_screen": {"status": "alerts" if blocking else "clear", "alerts": alerts, "blocking": len(blocking),
                             "release": result["release"], "completeness": result["completeness"],
                             "statement": (f"{len(blocking)} blocking alert(s) for the proposed orders." if blocking else
                                           "No blocking alert for the proposed orders in this knowledge release.")
                                          + " Screening is not proof of safety."}}


def safety_review_gate(state: dict) -> dict:
    """Gate 4: blocking alerts go back to the doctor once - override with a reason, or revise the orders."""
    doc = dict(state.get("documentation") or {})
    safety = doc.get("safety") or {}
    decision = _gate(state, "safety_review", {"changeset_id": safety.get("changeset_id"), "blocking": safety.get("blocking")})
    action = decision.get("action")
    with get_sessionmaker()() as db:
        cs = db.get(DocumentationChangeSet, uuid.UUID(safety["changeset_id"]))
        recorded = (cs.safety_decision or {}).get("action") if cs else None
    if action not in ("override", "revise") or recorded != action:
        return {"outcome": {"status": "blocked", "node": "safety_review", "code": "safety_decision_not_recorded"}}
    doc["safety_decision"] = {"action": action}
    return {"documentation": doc, "actor_user_id": decision.get("actor_user_id", state["actor_user_id"]),
            "actor_role": decision.get("actor_role", state["actor_role"])}


@agent_node("chart", "chart_commit", retries=0)
def chart_commit_node(state: dict, ctx: RunContext) -> dict:
    """Chart agent: one transaction writes the note, facts, orders and approvals, finalizes the encounter, audits."""
    changeset_id, sha = _submission(state)
    try:
        result = tools.invoke(ctx, "documentation.commit_approved", {"changeset_id": str(changeset_id), "expected_sha256": sha},
                              caller="agent:chart")
    except ds.DocumentationInvalid as exc:
        raise NodeBlocked("documentation_invalid", "; ".join(exc.problems)[:300])
    except tools.ToolDenied as exc:
        raise NodeBlocked(exc.code, str(exc))
    ctx.metrics.update(result["counts"])
    doc = dict(state.get("documentation") or {})
    doc.update({"status": "committed", "result": result})
    return {"documentation": doc}
