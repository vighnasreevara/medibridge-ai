"""The cardiology intake workflow as an explicit LangGraph state machine.

    supervisor_start -> registry_check -> scribe_transcribe -> [GATE transcript_review] -> scribe_extract
      -> chart_review -> evidence_retrieval -> medication_screen -> handoff_brief -> [GATE handoff_review]
      -(approve)-> order_draft -> [GATE orders_review] -(signed)-> order_safety_check
      -(clear)-> chart_commit -> after_visit_summary -> finalize
      -(blocking alerts)-> [GATE safety_review] -(override with reason)-> chart_commit
                                                -(revise)-> orders_review (new orders are screened again)
    handoff_review -(regenerate)-> handoff_brief;  -(reject)-> finalize;  orders_review -(cancel)-> finalize
Any critical failure or guardrail block routes straight to finalize. Nothing reaches the record without the
doctor's approvals.

The Care Orchestrator (this graph, its checkpoints and the four gates) runs in the API process. Every specialist
step is a proxy that calls its agent's A2A service (see ``a2a/``); with AGENT_TRANSPORT=inprocess the same node
implementations run locally instead.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .a2a.proxy import remote
from .a2a.registry import REMOTE_AGENTS, SKILL_BY_NODE
from .nodes.context import transcript_review_gate
from .nodes.handoff import handoff_review_gate
from .nodes.orders import orders_review_gate, safety_review_gate
from .nodes.supervisor import finalize, stopped, supervisor_start
from .state import IntakeState

LINEAR = [("supervisor_start", "registry_check"), ("registry_check", "scribe_transcribe"),
          ("scribe_transcribe", "transcript_review"), ("transcript_review", "scribe_extract"),
          ("scribe_extract", "chart_review"), ("chart_review", "evidence_retrieval"),
          ("evidence_retrieval", "medication_screen"), ("medication_screen", "handoff_brief"),
          ("handoff_brief", "handoff_review"), ("order_draft", "orders_review"),
          ("chart_commit", "after_visit_summary")]

NODE_AGENT = {"supervisor_start": "supervisor", "registry_check": "patient_registry",
              "scribe_transcribe": "ambient_scribe", "transcript_review": "supervisor", "scribe_extract": "ambient_scribe",
              "chart_review": "chart", "evidence_retrieval": "evidence_retrieval", "medication_screen": "medication_safety",
              "handoff_brief": "handoff_brief", "handoff_review": "supervisor", "order_draft": "order_entry",
              "orders_review": "supervisor", "order_safety_check": "order_entry", "safety_review": "supervisor",
              "chart_commit": "chart", "after_visit_summary": "after_visit_summary", "finalize": "supervisor"}
GATES = {"transcript_review", "handoff_review", "orders_review", "safety_review"}
LOCAL = {"supervisor_start": supervisor_start, "transcript_review": transcript_review_gate,
         "handoff_review": handoff_review_gate, "orders_review": orders_review_gate,
         "safety_review": safety_review_gate, "finalize": finalize}


def _next_or_finalize(target: str):
    def route(state: dict) -> str:
        return "finalize" if stopped(state) else target
    return route


def _after_handoff_review(state: dict) -> str:
    if stopped(state):
        return "finalize"
    action = (state.get("review") or {}).get("action")
    return {"approve": "order_draft", "regenerate": "handoff_brief"}.get(action, "finalize")


def _after_orders_review(state: dict) -> str:
    if stopped(state):
        return "finalize"
    action = ((state.get("documentation") or {}).get("submission") or {}).get("action")
    return "order_safety_check" if action == "submit" else "finalize"


def _after_safety_check(state: dict) -> str:
    if stopped(state):
        return "finalize"
    status = ((state.get("documentation") or {}).get("safety") or {}).get("status")
    return "chart_commit" if status == "clear" else "safety_review"


def _after_safety_review(state: dict) -> str:
    if stopped(state):
        return "finalize"
    action = ((state.get("documentation") or {}).get("safety_decision") or {}).get("action")
    return {"override": "chart_commit", "revise": "orders_review"}.get(action, "finalize")


def build_graph() -> StateGraph:
    g = StateGraph(IntakeState)
    for name in NODE_AGENT:
        g.add_node(name, LOCAL[name] if name in LOCAL else remote(name))
    g.add_edge(START, "supervisor_start")
    for src, dst in LINEAR:
        g.add_conditional_edges(src, _next_or_finalize(dst), [dst, "finalize"])
    g.add_conditional_edges("handoff_review", _after_handoff_review, ["order_draft", "handoff_brief", "finalize"])
    g.add_conditional_edges("orders_review", _after_orders_review, ["order_safety_check", "finalize"])
    g.add_conditional_edges("order_safety_check", _after_safety_check, ["chart_commit", "safety_review", "finalize"])
    g.add_conditional_edges("safety_review", _after_safety_review, ["chart_commit", "orders_review", "finalize"])
    g.add_edge("after_visit_summary", "finalize")
    g.add_edge("finalize", END)
    return g


def topology() -> dict:
    """Static graph for the Mission Control diagram (derived from the same definition the runtime executes)."""
    compiled = build_graph().compile()
    gr = compiled.get_graph()
    nodes = []
    for n in gr.nodes:
        if n in ("__start__", "__end__"):
            continue
        skill = SKILL_BY_NODE.get(n)
        nodes.append({"id": n, "agent": NODE_AGENT.get(n, n), "gate": n in GATES,
                      "remote": ({"skill": skill.id, "url": REMOTE_AGENTS[skill.agent_id].url,
                                  "peer_calls": list(skill.delegates)} if skill else None)})
    edges = [{"source": e.source, "target": e.target, "conditional": bool(e.conditional)} for e in gr.edges
             if e.source not in ("__start__",) and e.target not in ("__end__",)]
    return {"nodes": nodes, "edges": edges}
