"""A2A boundary: agent cards, capability tokens, state binding, data minimisation and response contracts.

Calls go through the real agent apps over httpx's in-process ASGI transport (see ``a2a_in_process``).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers import new_raw_part, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import Message, Role, SendMessageRequest, TaskState
from sqlalchemy import select
from starlette.applications import Starlette

from medibridge_agents.a2a import capability
from medibridge_agents.a2a import client as a2a_client
from medibridge_agents.a2a.registry import REMOTE_AGENTS, SKILL_BY_NODE
from medibridge_agents.a2a.server import build_card
from medibridge_api.models import AuditLog

JSON = "application/json"


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, a2a_client._background_loop()).result(60)


def ids(patient_id=None) -> dict:
    return {"run_id": str(uuid.uuid4()), "encounter_id": str(uuid.uuid4()),
            "patient_id": str(patient_id or uuid.uuid4()), "actor_user_id": str(uuid.uuid4()), "actor_role": "nurse"}


async def _send(agent_id: str, token: str, payload: dict):
    """Send one message with an explicit token and payload; returns the final task."""
    async with a2a_client._http(agent_id, 30, token) as http:
        card = await a2a_client._card(agent_id)
        sdk = ClientFactory(ClientConfig(streaming=False, httpx_client=http)).create(card)
        msg = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()),
                      parts=[new_raw_part(json.dumps(payload).encode(), media_type=JSON)])
        task = None
        async for resp in sdk.send_message(SendMessageRequest(message=msg)):
            if resp.HasField("task"):
                task = resp.task
        return task


def refusal_code(task) -> str:
    assert task.status.state == TaskState.TASK_STATE_REJECTED, TaskState.Name(task.status.state)
    return json.loads(task.status.message.parts[0].raw)["code"]


async def _post(agent_id: str, headers: dict) -> httpx.Response:
    async with a2a_client._http(agent_id, 30) as http:
        return await http.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}},
                               headers=headers)


@pytest.mark.parametrize("agent_id", sorted(REMOTE_AGENTS))
def test_agent_card_is_public_and_declares_skills_and_auth(agent_id):
    async def fetch():
        async with a2a_client._http(agent_id, 10) as http:
            return await http.get("/.well-known/agent-card.json")

    r = run(fetch())
    assert r.status_code == 200
    card = r.json()
    assert card["name"] == f"medibridge-{agent_id}"
    assert {s["id"] for s in card["skills"]} == {s.id for s in REMOTE_AGENTS[agent_id].skills}
    assert card["supportedInterfaces"][0]["url"].startswith("http://127.0.0.1:")
    assert "medibridgeCapability" in card["securitySchemes"]


def test_rpc_without_a_token_is_refused_and_audited(db):
    r = run(_post("chart", {}))
    assert r.status_code == 401 and r.json() == {"error": "capability_missing"}
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "a2a.capability_rejected")
                       .order_by(AuditLog.seq.desc())).first()
    assert entry.outcome == "denied" and entry.details["reason"] == "capability_missing"


def test_forged_expired_and_misdirected_tokens_are_refused():
    state = ids()
    good = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=state)
    body, sig = good.split(".")
    forged = f"{body}.{sig[:-2]}AA"
    expired = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=state, ttl_s=-5)
    other_agent = capability.mint(agent_id="evidence_retrieval", skill_id="retrieve_clinical_evidence", state=state)
    for token, code in ((forged, "capability_bad_signature"), (expired, "capability_expired"),
                        (other_agent, "capability_wrong_agent"), ("not-a-token", "capability_malformed")):
        r = run(_post("chart", {"Authorization": f"Bearer {token}"}))
        assert (r.status_code, r.json()["error"]) == (401, code)


def test_a_token_is_accepted_only_once():
    state = ids()
    token = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=state)
    ledger = capability.NonceLedger()
    capability.verify(token, agent_id="chart", ledger=ledger)
    with pytest.raises(capability.CapabilityError) as exc:
        capability.verify(token, agent_id="chart", ledger=ledger)
    assert exc.value.code == "capability_replayed"
    # and over the wire: the first use consumes the nonce, the second request is refused before the body is read
    token = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=state)
    run(_send("chart", token, {"skill": "assemble_patient_chart", "state": {**state, "steps": 0}}))
    r = run(_post("chart", {"Authorization": f"Bearer {token}"}))
    assert (r.status_code, r.json()["error"]) == (401, "capability_replayed")


def test_state_for_another_patient_is_refused_and_audited(db):
    issued_for = ids()
    other = {**issued_for, "patient_id": str(uuid.uuid4())}
    token = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=issued_for)
    task = run(_send("chart", token, {"skill": "assemble_patient_chart", "state": other}))
    assert refusal_code(task) == "capability_state_mismatch"
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "a2a.task_refused")
                       .order_by(AuditLog.seq.desc())).first()
    assert entry.details["reason"] == "capability_state_mismatch"
    assert str(entry.patient_id) == issued_for["patient_id"]


def test_a_skill_the_agent_does_not_offer_is_refused():
    state = ids()
    token = capability.mint(agent_id="chart", skill_id="generate_handoff_brief", state=state)
    task = run(_send("chart", token, {"skill": "generate_handoff_brief", "state": state}))
    assert refusal_code(task) == "skill_not_offered"


def test_inputs_outside_the_skill_allow_list_are_refused():
    state = ids()
    token = capability.mint(agent_id="patient_registry", skill_id="verify_encounter_identity", state=state)
    # The identity agent needs no clinical data; sending findings is over-sharing and is refused.
    task = run(_send("patient_registry", token, {"skill": "verify_encounter_identity",
                                         "state": {**state, "findings": {"symptoms": []}}}))
    assert refusal_code(task) == "input_not_allowed"


def test_supervisor_sends_only_allowed_inputs(a2a_in_process):
    seen = {}

    class Recorder(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
            seen.update(json.loads(context.message.parts[0].raw)["state"])
            up = TaskUpdater(event_queue, context.task_id, context.context_id)
            await up.add_artifact([new_raw_part(json.dumps({"skill": "screen_current_medications",
                                                            "delta": {"medication_safety": {"status": "screened"}}}).encode(),
                                                media_type=JSON)], name="state_delta")
            await up.complete()

        async def cancel(self, context, event_queue):
            pass

    a2a_in_process["medication_safety"] = _app("medication_safety", Recorder())
    state = {**ids(), "steps": 3, "findings": {"medications": []}, "history": {"allergies": []},
             "transcript": {"segments": [{"text": "not for this agent"}]}, "handoff": {"version": 1}}
    res = a2a_client.call(SKILL_BY_NODE["medication_screen"], state)
    assert set(seen) == {"run_id", "encounter_id", "patient_id", "actor_user_id", "actor_role", "steps",
                         "findings", "history"}
    assert res.delta == {"medication_safety": {"status": "screened"}}


def _app(agent_id: str, executor: AgentExecutor) -> Starlette:
    card = build_card(REMOTE_AGENTS[agent_id])
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/a2a")])


def test_a_rogue_agent_cannot_write_outside_its_contract(a2a_in_process):
    class Rogue(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
            up = TaskUpdater(event_queue, context.task_id, context.context_id)
            delta = {"history": {}, "review": {"action": "approve"}}  # tries to approve the handoff
            await up.add_artifact([new_raw_part(json.dumps({"skill": "assemble_patient_chart", "delta": delta})
                                                .encode(), media_type=JSON)], name="state_delta")
            await up.complete()

        async def cancel(self, context, event_queue):
            pass

    a2a_in_process["chart"] = _app("chart", Rogue())
    with pytest.raises(a2a_client.AgentRefused) as exc:
        a2a_client.call(SKILL_BY_NODE["chart_review"], {**ids(), "steps": 0, "findings": {}})
    assert exc.value.code == "output_not_allowed" and exc.value.detected_by_supervisor


def test_an_agent_that_is_down_is_reported_as_unreachable(a2a_in_process):
    a2a_in_process["evidence_retrieval"] = None
    started = time.perf_counter()
    with pytest.raises(a2a_client.AgentUnavailable) as exc:
        a2a_client.call(SKILL_BY_NODE["evidence_retrieval"], {**ids(), "steps": 0})
    assert exc.value.code == "agent_unreachable" and time.perf_counter() - started < 10


def test_a_peer_only_skill_refuses_a_direct_supervisor_call():
    state = ids()
    token = capability.mint(agent_id="medication_safety", skill_id="screen_proposed_orders", state=state)
    task = run(_send("medication_safety", token, {"skill": "screen_proposed_orders", "state": state}))
    assert refusal_code(task) == "peer_only"


def test_a_delegated_capability_cannot_be_used_for_another_skill():
    state = ids()
    token = capability.mint(agent_id="medication_safety", skill_id="screen_current_medications", state=state,
                            via="order_entry")
    task = run(_send("medication_safety", token, {"skill": "screen_current_medications", "state": state}))
    assert refusal_code(task) == "delegated_capability_misuse"


def test_an_agent_only_receives_the_delegations_its_skill_declares():
    state = ids()
    token = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=state)
    task = run(_send("chart", token, {"skill": "assemble_patient_chart", "state": state,
                                      "delegations": {"medication_safety/screen_proposed_orders": "x"}}))
    assert refusal_code(task) == "delegation_not_allowed"


def test_run_scoped_skills_refuse_an_actor_only_capability():
    actor = {k: v for k, v in ids().items() if k in ("actor_user_id", "actor_role")}
    token = capability.mint(agent_id="chart", skill_id="assemble_patient_chart", state=actor)
    task = run(_send("chart", token, {"skill": "assemble_patient_chart", "state": actor}))
    assert refusal_code(task) == "capability_not_run_scoped"


def test_the_supervisor_delegates_exactly_the_declared_peer_call(a2a_in_process):
    seen = {}

    class Recorder(AgentExecutor):
        async def execute(self, context, event_queue):
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
            seen.update(json.loads(context.message.parts[0].raw))
            up = TaskUpdater(event_queue, context.task_id, context.context_id)
            await up.add_artifact([new_raw_part(json.dumps({"skill": "verify_proposed_orders", "delta": {}}).encode(),
                                                media_type=JSON)], name="state_delta")
            await up.complete()

        async def cancel(self, context, event_queue):
            pass

    a2a_in_process["order_entry"] = _app("order_entry", Recorder())
    a2a_client.call(SKILL_BY_NODE["order_safety_check"], {**ids(), "steps": 0, "documentation": {}})
    assert set(seen["delegations"]) == {"medication_safety/screen_proposed_orders"}
    claims = json.loads(capability._unb64(seen["delegations"]["medication_safety/screen_proposed_orders"].split(".")[0]))
    assert claims["aud"] == "medication_safety" and claims["via"] == "order_entry"
    assert claims["skill"] == "screen_proposed_orders" and claims["pat"] == seen["state"]["patient_id"]
