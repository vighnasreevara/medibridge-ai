"""Supervisor-side A2A client: send one skill request to one remote agent and return its state delta.

Graph nodes are synchronous and run in the runner's worker threads; A2A calls run on one long-lived event
loop in a background thread. Agent cards are fetched from ``/.well-known/agent-card.json`` and cached; a card
that does not offer the requested skill is refused before any data is sent.
"""
from __future__ import annotations

import asyncio
import functools
import json
import ssl
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.helpers import new_raw_part
from a2a.types import AgentCard, Message, Role, SendMessageConfiguration, SendMessageRequest, TaskState

from medibridge_api.config import get_settings

from . import capability
from .registry import REMOTE_AGENTS, Skill

JSON = "application/json"
CARD_TTL_S = 60


class AgentUnavailable(Exception):
    """The agent could not be reached or did not answer in time."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AgentRefused(Exception):
    """The call was refused (protocol-level, not a clinical result): by the agent, or by the supervisor
    because the agent's card or response broke the contract (``detected_by_supervisor``)."""

    def __init__(self, code: str, detected_by_supervisor: bool = False):
        super().__init__(code)
        self.code, self.detected_by_supervisor = code, detected_by_supervisor


@dataclass
class CallResult:
    delta: dict
    endpoint: str
    task_state: str
    request_bytes: int
    response_bytes: int


# Tests replace this to route calls to in-process ASGI apps instead of real ports.
TransportFactory = Callable[[str], httpx.AsyncBaseTransport | None]
_transport_factory: TransportFactory = lambda agent_id: None  # noqa: E731
_cards: dict[str, tuple[float, AgentCard]] = {}
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def use_transport(factory: TransportFactory | None) -> None:
    global _transport_factory
    _transport_factory = factory or (lambda agent_id: None)
    _cards.clear()


def _background_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="medibridge-a2a", daemon=True).start()
        return _loop


@functools.cache
def _tls() -> ssl.SSLContext:
    # Agent URLs are plain-HTTP loopback, so this is never used for a handshake. Building it once matters:
    # httpx otherwise loads the CA bundle for every client (about 0.5 s on Windows, blocking the event loop).
    return ssl.create_default_context()


def _http(agent_id: str, timeout_s: float, token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=_transport_factory(agent_id), base_url=REMOTE_AGENTS[agent_id].url,
                             headers=headers, trust_env=False, verify=_tls(),
                             timeout=httpx.Timeout(timeout_s, connect=5.0))


async def _card(agent_id: str) -> AgentCard:
    cached = _cards.get(agent_id)
    if cached and time.monotonic() - cached[0] < CARD_TTL_S:
        return cached[1]
    async with _http(agent_id, 10) as http:
        card = await A2ACardResolver(http, REMOTE_AGENTS[agent_id].url).get_agent_card()
    _cards[agent_id] = (time.monotonic(), card)
    return card


def _first_json(parts) -> dict:
    for p in parts:
        if p.WhichOneof("content") == "raw" and p.media_type == JSON:
            return json.loads(p.raw)
    return {}


async def _send(skill: Skill, state: dict, parent_execution_id: uuid.UUID | None, token: str | None) -> CallResult:
    settings = get_settings()
    agent = REMOTE_AGENTS[skill.agent_id]
    card = await _card(agent.id)
    if skill.id not in {s.id for s in card.skills}:
        raise AgentRefused("skill_not_advertised", detected_by_supervisor=True)
    keys = (*skill.id_keys, "steps", *skill.inputs) if skill.run_scoped else (*skill.id_keys, *skill.inputs)
    sent = {k: state[k] for k in keys if k in state}
    payload: dict = {"skill": skill.id, "state": sent}
    if token is None:
        token = capability.mint(agent_id=agent.id, skill_id=skill.id, state=sent,
                                parent_execution_id=parent_execution_id)
        # Peer calls this skill may make: one capability each, naming this agent, bound to the same run.
        if skill.delegates:
            payload["delegations"] = {
                target: capability.mint(agent_id=target.split("/")[0], skill_id=target.split("/")[1], state=sent,
                                        parent_execution_id=parent_execution_id, via=agent.id)
                for target in skill.delegates}
    body = json.dumps(payload, separators=(",", ":")).encode()
    message = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()),
                      parts=[new_raw_part(body, media_type=JSON)])
    request = SendMessageRequest(message=message, configuration=SendMessageConfiguration(
        accepted_output_modes=[JSON], return_immediately=False))
    async with _http(agent.id, settings.a2a_call_timeout_s, token) as http:
        client = ClientFactory(ClientConfig(streaming=False, httpx_client=http,
                                            supported_protocol_bindings=["JSONRPC"])).create(card)
        task = None
        async for response in client.send_message(request):
            if response.HasField("task"):
                task = response.task
    if task is None:
        raise AgentRefused("no_task_returned", detected_by_supervisor=True)
    state_name = TaskState.Name(task.status.state)
    if task.status.state != TaskState.TASK_STATE_COMPLETED:
        detail = _first_json(task.status.message.parts) if task.status.HasField("message") else {}
        raise AgentRefused(str(detail.get("code") or state_name.lower()))
    artifact = next((a for a in task.artifacts if a.name == "state_delta"), None)
    payload = _first_json(artifact.parts) if artifact else {}
    if payload.get("skill") != skill.id or not isinstance(payload.get("delta"), dict):
        raise AgentRefused("bad_response", detected_by_supervisor=True)
    delta = payload["delta"]
    if set(delta) - skill.allowed_outputs:
        raise AgentRefused("output_not_allowed", detected_by_supervisor=True)
    return CallResult(delta, card.supported_interfaces[0].url, state_name, len(body),
                      len(artifact.parts[0].raw) if artifact else 0)


def _status_code(exc: BaseException) -> int | None:
    seen = exc
    while seen is not None:
        if isinstance(seen, httpx.HTTPStatusError):
            return seen.response.status_code
        seen = seen.__cause__ or seen.__context__
    return None


def call(skill: Skill, state: dict, parent_execution_id: uuid.UUID | None = None, token: str | None = None) -> CallResult:
    """Blocking call used by graph nodes, the API and peer calls. Raises AgentUnavailable or AgentRefused.

    ``token``: a capability minted elsewhere (a delegated peer call); otherwise one is minted here."""
    future = asyncio.run_coroutine_threadsafe(_send(skill, state, parent_execution_id, token), _background_loop())
    try:
        return future.result(timeout=get_settings().a2a_call_timeout_s + 30)
    except AgentRefused:
        raise
    except (TimeoutError, A2AClientTimeoutError, httpx.TimeoutException):
        future.cancel()
        raise AgentUnavailable("agent_timeout") from None
    except httpx.TransportError:
        raise AgentUnavailable("agent_unreachable") from None
    except (A2AClientError, httpx.HTTPError) as exc:
        if isinstance(exc.__cause__, httpx.TransportError) or isinstance(exc.__context__, httpx.TransportError):
            raise AgentUnavailable("agent_unreachable") from None
        code = _status_code(exc)
        raise AgentRefused("capability_rejected" if code == 401 else f"a2a_error_{code or type(exc).__name__}") from None
