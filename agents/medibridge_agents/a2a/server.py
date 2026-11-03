"""One specialist agent as an A2A service (JSON-RPC binding, A2A protocol 1.0).

Request path:
1. ``CapabilityMiddleware`` rejects the HTTP request (401) unless it carries a valid capability token for this
   agent: signature, audience, expiry and single use are checked before the body is read.
2. ``NodeExecutor`` checks the token names one of this agent's skills, that the run identifiers in the state
   equal the token's claims, and that only the skill's input keys were sent. It then runs the node
   implementation (the same ``agent_node``-wrapped function, with its telemetry, retries and tool-layer
   permissions) and returns only the skill's output keys as a JSON artifact.

State travels as one ``application/json`` raw part rather than a protobuf ``Struct``, which would turn every
integer into a float.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from a2a.helpers import new_raw_part, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill,
                       HTTPAuthSecurityScheme, SecurityRequirement, SecurityScheme)
from a2a.utils.constants import PROTOCOL_VERSION_1_0
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from medibridge_api.routes.governance import load_runtime_settings
from medibridge_api.services import audit

from . import capability, peer
from .registry import REMOTE_AGENTS, RemoteAgent

log = logging.getLogger("medibridge.a2a")
RPC_PATH = "/a2a"
JSON = "application/json"
SCHEME = "medibridgeCapability"
AGENT_VERSION = "1.0.0"


def build_card(agent: RemoteAgent) -> AgentCard:
    return AgentCard(
        name=f"medibridge-{agent.id}", description=agent.description, version=AGENT_VERSION,
        provider=AgentProvider(organization="MediBridge AI (synthetic-data demo)", url=agent.url),
        supported_interfaces=[AgentInterface(url=f"{agent.url}{RPC_PATH}", protocol_binding="JSONRPC",
                                             protocol_version=PROTOCOL_VERSION_1_0)],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        security_schemes={SCHEME: SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(
            scheme="bearer", bearer_format="MediBridge capability token (HMAC, single use)",
            description="Issued per call by the MediBridge supervisor; bound to run, patient, agent and skill."))},
        security_requirements=[SecurityRequirement(schemes={SCHEME: {}})],
        default_input_modes=[JSON], default_output_modes=[JSON],
        skills=[AgentSkill(id=s.id, name=s.name, description=s.description, tags=list(s.tags),
                           input_modes=[JSON], output_modes=[JSON]) for s in agent.skills])


class CapabilityMiddleware:
    """Pure ASGI: only the agent card is public; every RPC needs a valid, unused capability token."""

    def __init__(self, app, agent_id: str, ledger: capability.NonceLedger):
        self.app, self.agent_id, self.ledger = app, agent_id, ledger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != RPC_PATH:
            return await self.app(scope, receive, send)
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        try:
            if not auth.lower().startswith("bearer "):
                raise capability.CapabilityError("capability_missing")
            scope["medibridge_capability"] = capability.verify(auth[7:].strip(), agent_id=self.agent_id,
                                                               ledger=self.ledger)
        except capability.CapabilityError as exc:
            log.warning("a2a %s rejected a call: %s", self.agent_id, exc.code)
            await asyncio.to_thread(audit.record_now, action="a2a.capability_rejected", category="security",
                                    outcome="denied", actor_role="system:a2a", resource_type="agent",
                                    resource_id=self.agent_id, details={"reason": exc.code})
            response = JSONResponse({"error": exc.code}, status_code=401,
                                    headers={"WWW-Authenticate": f'Bearer realm="{self.agent_id}"'})
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


class CapabilityContextBuilder(DefaultServerCallContextBuilder):
    def build(self, request: Request):
        ctx = super().build(request)
        ctx.state["headers"].pop("authorization", None)  # the token stays out of task state and logs
        ctx.state["capability"] = request.scope.get("medibridge_capability")
        return ctx


def _audit_task_refusal(agent_id: str, claims: dict | None, code: str) -> None:
    claims = claims or {}

    def as_uuid(value):
        try:
            return uuid.UUID(str(value))
        except ValueError:
            return None

    audit.record_now(action="a2a.task_refused", category="security", outcome="denied",
                     actor_user_id=as_uuid(claims.get("act")), actor_role=claims.get("role"),
                     patient_id=as_uuid(claims.get("pat")), encounter_id=as_uuid(claims.get("enc")),
                     resource_type="agent", resource_id=agent_id,
                     details={"reason": code, "skill": claims.get("skill"), "run_id": claims.get("run")})


def _json_part(obj: dict):
    return new_raw_part(json.dumps(obj, separators=(",", ":")).encode(), media_type=JSON)


class NodeExecutor(AgentExecutor):
    def __init__(self, agent: RemoteAgent):
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(context.message))
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        async def refuse(code: str) -> None:
            log.warning("a2a %s refused a task: %s", self.agent.id, code)
            await asyncio.to_thread(_audit_task_refusal, self.agent.id, claims, code)
            await updater.reject(updater.new_agent_message([_json_part({"code": code})]))

        claims = (context.call_context.state if context.call_context else {}).get("capability")
        if not claims:
            return await refuse("capability_missing")
        skill = self.agent.skill(claims.get("skill", ""))
        if skill is None:
            return await refuse("skill_not_offered")
        parts = [p for p in context.message.parts if p.WhichOneof("content") == "raw" and p.media_type == JSON]
        try:
            payload = json.loads(parts[0].raw)
        except (IndexError, ValueError):
            payload = None
        state = payload.get("state") if isinstance(payload, dict) else None
        if not isinstance(state, dict) or payload.get("skill") != skill.id:
            return await refuse("bad_payload")
        if skill.run_scoped and not all(claims.get(c) for c in ("run", "enc", "pat")):
            return await refuse("capability_not_run_scoped")
        if claims.get("via") != skill.peer_from:
            return await refuse("peer_only" if skill.peer_from else "delegated_capability_misuse")
        if capability.binding_mismatches(claims, state):
            return await refuse("capability_state_mismatch")
        extra = set(state) - {*skill.id_keys, "steps", *skill.inputs}
        if extra:
            return await refuse("input_not_allowed")
        granted = payload.get("delegations") or {}
        if not isinstance(granted, dict) or set(granted) - set(skill.delegates):
            return await refuse("delegation_not_allowed")

        await updater.start_work()
        node_state = {**state, "parent_execution_id": claims.get("parent")} if skill.run_scoped else dict(state)
        peer.delegations.set({k: str(v) for k, v in granted.items()})
        try:
            load_runtime_settings()  # pick up model changes made in Administration since this service started
            result = await asyncio.to_thread(skill.fn, node_state)
        except Exception as exc:  # noqa: BLE001 - agent_node already catches node errors; this is a service bug
            log.exception("a2a %s skill %s crashed", self.agent.id, skill.id)
            return await updater.failed(updater.new_agent_message([_json_part(
                {"code": "agent_internal_error", "error_class": type(exc).__name__})]))
        delta = {k: v for k, v in (result or {}).items() if k in skill.allowed_outputs}
        dropped = sorted(set(result or {}) - set(delta))
        if dropped:
            log.warning("a2a %s skill %s produced keys outside its contract: %s", self.agent.id, skill.id, dropped)
        await updater.add_artifact([_json_part({"skill": skill.id, "delta": delta})], name="state_delta")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Node implementations are synchronous and not interruptible; cancellation is not offered.
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.reject(updater.new_agent_message([_json_part({"code": "cancel_not_supported"})]))


def create_app(agent_id: str) -> Starlette:
    agent = REMOTE_AGENTS[agent_id]
    for skill in agent.skills:
        skill.fn  # noqa: B018 - import the agent's code now, not on the first (timed) request
    card = build_card(agent)
    handler = DefaultRequestHandler(agent_executor=NodeExecutor(agent), task_store=InMemoryTaskStore(),
                                    agent_card=card)
    app = Starlette(routes=[*create_agent_card_routes(card, cache_control="no-cache"),
                            *create_jsonrpc_routes(handler, RPC_PATH, context_builder=CapabilityContextBuilder())])
    app.add_middleware(CapabilityMiddleware, agent_id=agent_id, ledger=capability.NonceLedger())
    app.state.agent_id = agent_id
    return app

