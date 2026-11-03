"""Live status of the agent services, read from their agent cards (nothing is assumed up)."""
from __future__ import annotations

import asyncio
import time

from a2a.client import A2ACardResolver

from medibridge_api.config import get_settings

from . import client
from .registry import REMOTE_AGENTS, RemoteAgent
from .server import RPC_PATH


async def _probe(agent: RemoteAgent, timeout_s: float) -> dict:
    out = {"id": agent.id, "label": agent.label, "url": agent.url, "rpc_url": f"{agent.url}{RPC_PATH}",
           "card_url": f"{agent.url}/.well-known/agent-card.json", "port": agent.port,
           "skills": [s.id for s in agent.skills], "status": "down", "latency_ms": None, "problem": None}
    started = time.perf_counter()
    try:
        async with client._http(agent.id, timeout_s) as http:
            card = await A2ACardResolver(http, agent.url).get_agent_card()
    except Exception as exc:  # noqa: BLE001 - reported as the probe result
        out["problem"] = type(exc).__name__
        return out
    out["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    advertised = sorted(s.id for s in card.skills)
    out["card"] = {"name": card.name, "version": card.version, "skills": advertised,
                   "protocol": [f"{i.protocol_binding} {i.protocol_version}" for i in card.supported_interfaces]}
    if advertised != sorted(out["skills"]):
        out["status"], out["problem"] = "mismatch", "card skills differ from the supervisor's registry"
    else:
        out["status"] = "up"
    return out


def probe_all(timeout_s: float = 2.0) -> dict:
    transport = get_settings().agent_transport
    if transport == "inprocess":
        return {"transport": transport, "agents": [
            {"id": a.id, "label": a.label, "skills": [s.id for s in a.skills], "status": "in_process"}
            for a in REMOTE_AGENTS.values()]}

    async def gather():
        return await asyncio.gather(*(_probe(a, timeout_s) for a in REMOTE_AGENTS.values()))

    future = asyncio.run_coroutine_threadsafe(gather(), client._background_loop())
    return {"transport": transport, "agents": future.result(timeout_s + 5)}
