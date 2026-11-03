"""Agent-to-agent calls made by an agent itself (not by the supervisor).

The supervisor decides which peer calls a skill may make (``Skill.delegates``) and mints a capability for each
one, bound to the same run, patient and actor and naming the calling agent (``via``). The agent service puts
those capabilities in a context variable for the node; ``call`` uses them and cannot reach any other agent or
skill. With AGENT_TRANSPORT=inprocess the peer skill runs in this process instead.
"""
from __future__ import annotations

from contextvars import ContextVar

from medibridge_api.config import get_settings

from .registry import REMOTE_AGENTS

delegations: ContextVar[dict[str, str]] = ContextVar("a2a_delegations", default={})


class PeerUnavailable(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def call(agent_id: str, skill_id: str, state: dict) -> dict:
    """Run a peer skill and return its state delta. Raises PeerUnavailable on any transport or refusal."""
    from . import client

    skill = REMOTE_AGENTS[agent_id].skill(skill_id)
    if get_settings().agent_transport == "inprocess":
        return skill.fn(state) or {}
    token = delegations.get().get(f"{agent_id}/{skill_id}")
    if not token:
        raise PeerUnavailable("delegation_missing")
    try:
        return client.call(skill, state, token=token).delta
    except (client.AgentUnavailable, client.AgentRefused) as exc:
        raise PeerUnavailable(exc.code) from None
