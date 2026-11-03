"""Dependency health probes. Each probe reports what it actually observed; nothing is assumed healthy."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text

from .config import get_settings
from .db import get_engine
from .services.local_http import client as http

Status = Literal["pass", "warn", "fail"]


@dataclass
class ComponentHealth:
    id: str
    label: str
    status: Status
    summary: str
    latency_ms: float | None = None
    details: dict = field(default_factory=dict)


def _timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, round((time.perf_counter() - start) * 1000, 1)


def check_postgres() -> ComponentHealth:
    try:
        (row, ms) = _timed(lambda: _pg_query())
        return ComponentHealth("postgres", "PostgreSQL", "pass", f"PostgreSQL {row}", ms)
    except Exception as exc:  # noqa: BLE001 - surfaced to operators as a failed probe
        return ComponentHealth("postgres", "PostgreSQL", "fail", f"Unreachable: {type(exc).__name__}")


def _pg_query() -> str:
    with get_engine().connect() as conn:
        return conn.execute(text("SHOW server_version")).scalar_one()


def check_qdrant() -> ComponentHealth:
    s = get_settings()
    try:
        (resp, ms) = _timed(
            lambda: http().get(f"{s.qdrant_url}/readyz", headers={"api-key": s.qdrant_api_key.get_secret_value()}, timeout=3)
        )
        if resp.status_code != 200:
            return ComponentHealth("qdrant", "Qdrant", "fail", f"HTTP {resp.status_code}", ms)
        version = http().get(s.qdrant_url, headers={"api-key": s.qdrant_api_key.get_secret_value()}, timeout=3).json().get("version")
        return ComponentHealth("qdrant", "Qdrant", "pass", f"Qdrant {version}", ms)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("qdrant", "Qdrant", "fail", f"Unreachable: {type(exc).__name__}")


def check_neo4j() -> ComponentHealth:
    from neo4j import GraphDatabase

    s = get_settings()
    try:
        with GraphDatabase.driver(s.neo4j_uri, auth=("neo4j", s.neo4j_password.get_secret_value()),
                                  connection_timeout=3) as driver:
            (info, ms) = _timed(lambda: driver.get_server_info())
        return ComponentHealth("neo4j", "Neo4j", "pass", f"{info.agent}", ms)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("neo4j", "Neo4j", "fail", f"Unreachable: {type(exc).__name__}")


def check_ollama() -> ComponentHealth:
    s = get_settings()
    required = [s.llm_model_clinical, s.llm_model_light, s.embedding_model]
    try:
        (resp, ms) = _timed(lambda: http().get(f"{s.ollama_url}/api/version", timeout=3))
        version = resp.json().get("version")
        tags = http().get(f"{s.ollama_url}/api/tags", timeout=5).json().get("models", [])
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("ollama", "Ollama", "fail", f"Unreachable: {type(exc).__name__}")
    installed = {m["name"] for m in tags} | {m["name"].removesuffix(":latest") for m in tags}
    missing = [m for m in required if m not in installed]
    details = {"version": version, "required": required, "missing": missing}
    if missing:
        return ComponentHealth("ollama", "Ollama", "warn", f"Ollama {version}; missing models: {', '.join(missing)}",
                               ms, details)
    return ComponentHealth("ollama", "Ollama", "pass", f"Ollama {version}; {len(required)} required models present",
                           ms, details)


def check_knowledge() -> ComponentHealth:
    try:
        from .services.knowledge import safe_graph

        (kg, ms) = _timed(safe_graph)
        r = kg.release or {}
        return ComponentHealth("knowledge", "Knowledge graph", "pass", f"Release {r.get('id')}", ms,
                               {"release": r.get("id"), "built_at": r.get("built_at")})
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("knowledge", "Knowledge graph", "fail", f"No active release: {type(exc).__name__}")


def check_index() -> ComponentHealth:
    try:
        from .services.vector_index import stats

        (st, ms) = _timed(stats)
        if not st.get("collection"):
            return ComponentHealth("vector_index", "Document index", "warn", "No index built yet", ms, st)
        return ComponentHealth("vector_index", "Document index", "pass", f"{st['points']} chunks in {st['collection']}",
                               ms, st)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("vector_index", "Document index", "fail", f"Unavailable: {type(exc).__name__}")


def check_asr() -> ComponentHealth:
    from .providers import asr

    cached = asr.is_model_cached()
    return ComponentHealth("asr", "Speech recognition", "pass" if cached else "warn",
                           f"{asr.model_name()} {'ready (local)' if cached else 'not downloaded yet (first use downloads it)'}")


def check_agents() -> ComponentHealth:
    """The A2A agent services: each must serve an agent card that offers the skills the supervisor expects."""
    try:
        from medibridge_agents.a2a.status import probe_all

        (result, ms) = _timed(probe_all)
    except Exception as exc:  # noqa: BLE001
        return ComponentHealth("agents", "Agent services (A2A)", "fail", f"Probe failed: {type(exc).__name__}")
    agents = result["agents"]
    details = {"transport": result["transport"],
               "agents": {a["id"]: {"status": a["status"], "url": a.get("url")} for a in agents}}
    if result["transport"] == "inprocess":
        return ComponentHealth("agents", "Agent services (A2A)", "warn",
                               "AGENT_TRANSPORT=inprocess: agents run inside the API process", ms, details)
    down = [a["id"] for a in agents if a["status"] != "up"]
    if down:
        return ComponentHealth("agents", "Agent services (A2A)", "fail",
                               f"{len(agents) - len(down)}/{len(agents)} up; not available: {', '.join(down)}", ms, details)
    return ComponentHealth("agents", "Agent services (A2A)", "pass", f"{len(agents)}/{len(agents)} agent cards served",
                           ms, details)


PROBES = (check_postgres, check_qdrant, check_neo4j, check_ollama, check_knowledge, check_index, check_asr,
          check_agents)


def run_health() -> dict:
    components = [probe() for probe in PROBES]
    worst: Status = "pass"
    for c in components:
        if c.status == "fail":
            worst = "fail"
        elif c.status == "warn" and worst == "pass":
            worst = "warn"
    return {
        "status": worst,
        "checked_at": datetime.now(UTC).isoformat(),
        "components": [asdict(c) for c in components],
    }
