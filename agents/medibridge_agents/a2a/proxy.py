"""Graph nodes that delegate to remote agents over A2A.

The supervisor keeps the LangGraph state, checkpoints and the three human gates. Each specialist step is a
proxy: it records an ``a2a`` execution (the remote agent's own execution becomes its child), sends the
skill's allowed inputs with a fresh capability token, and merges only the skill's allowed outputs.

Failure policy (same as in-process):
- agent unreachable or timed out: non-critical skills degrade (``{"status": "unavailable"}``), critical skills
  fail the run;
- agent refused the call (bad token, state mismatch, contract violation): the run is blocked and the refusal is
  audited, because it means misconfiguration or tampering, not a clinical result.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from medibridge_api.config import get_settings
from medibridge_api.services import audit

from ..runtime import RunContext, finish_execution, start_execution
from . import client
from .registry import SKILL_BY_NODE

log = logging.getLogger("medibridge.a2a")
UNREACHABLE_RETRIES = 1


def _audit_violation(ctx: RunContext, agent_id: str, skill_id: str, code: str) -> None:
    """A response the supervisor itself rejected (refusals by the agent are audited by the agent)."""
    audit.record_now(action="a2a.response_rejected", category="security", outcome="denied",
                     actor_user_id=ctx.actor_user_id, actor_role=ctx.actor_role, patient_id=ctx.patient_id,
                     encounter_id=ctx.encounter_id, resource_type="agent", resource_id=agent_id,
                     details={"skill": skill_id, "reason": code, "run_id": str(ctx.run_id)})


def remote(node: str) -> Callable[[dict], dict]:
    skill = SKILL_BY_NODE[node]

    def run(state: dict) -> dict:
        if get_settings().agent_transport == "inprocess":
            return skill.fn(state)
        ctx = RunContext.from_state(state)
        steps = int(state.get("steps", 0)) + 1
        target = f"{skill.agent_id}/{skill.id}"
        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            ex_id = start_execution(ctx, "supervisor", node, "a2a", attempt, tool_name=target)
            base = {"target_agent": skill.agent_id, "skill": skill.id, "protocol": "a2a/1.0 jsonrpc"}
            try:
                res = client.call(skill, state, parent_execution_id=ex_id)
            except client.AgentUnavailable as exc:
                finish_execution(ctx, ex_id, "failed", started=started, error_code=exc.code,
                                 error_class="AgentUnavailable", metrics=base, kind="a2a")
                if attempt <= UNREACHABLE_RETRIES and exc.code == "agent_unreachable":
                    time.sleep(2)
                    continue
                if skill.degrade_key:
                    return {"steps": steps, skill.degrade_key: {"status": "unavailable", "reason": exc.code}}
                return {"steps": steps, "outcome": {"status": "failed", "node": node, "code": exc.code}}
            except client.AgentRefused as exc:
                finish_execution(ctx, ex_id, "blocked", started=started, error_code=exc.code,
                                 error_class="AgentRefused", metrics=base, kind="a2a")
                if exc.detected_by_supervisor:
                    _audit_violation(ctx, skill.agent_id, skill.id, exc.code)
                return {"steps": steps, "outcome": {"status": "blocked", "node": node, "code": f"a2a_{exc.code}",
                                                    "message": "An agent refused the request; the run was stopped."}}
            finish_execution(ctx, ex_id, "succeeded", started=started, kind="a2a",
                             metrics={**base, "endpoint": res.endpoint, "task_state": res.task_state,
                                      "request_bytes": res.request_bytes, "response_bytes": res.response_bytes,
                                      "keys_returned": sorted(res.delta)})
            return {"steps": steps, **res.delta}

    run.__name__ = f"a2a_{node}"
    run.agent_id = skill.agent_id  # type: ignore[attr-defined]
    return run
