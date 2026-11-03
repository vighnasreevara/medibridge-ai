"""MediBridge MCP server (Model Context Protocol, SDK v2 ``MCPServer``).

Exposes the READ-ONLY clinical tools to an external, locally run MCP client (stdio). Privileged write tools
(handoff drafts, documentation commit) are deliberately not exposed: they exist only in-process behind
caller allow-lists and the human approval gates.

Binding and authorization:
- MEDIBRIDGE_MCP_SESSION: a MediBridge session token of a signed-in clinician. It is re-validated on EVERY call
  (expiry, idle timeout, role changes and sign-out all take effect immediately).
- MEDIBRIDGE_MCP_RUN_ID: the workflow run this session is bound to. The patient and encounter come from the run
  on the server; tools never accept a patient chosen by the client, and any id the client echoes must match.
Each call goes through ``medibridge_agents.tools.invoke`` (RBAC, care-team scope, id checks, schema validation,
telemetry) and is recorded in the audit log.

    MEDIBRIDGE_MCP_SESSION=... MEDIBRIDGE_MCP_RUN_ID=... uv run python -m medibridge_mcp
"""
from __future__ import annotations

import inspect
import os
import uuid
from typing import Any

from mcp.server.mcpserver import MCPServer

from medibridge_agents import tools
from medibridge_agents.runtime import RunContext
from medibridge_api.db import get_sessionmaker
from medibridge_api.models import WorkflowRun
from medibridge_api.security import sessions
from medibridge_api.services import audit


class SessionInvalid(Exception):
    pass


def _context(session_token: str, run_id: uuid.UUID) -> RunContext:
    with get_sessionmaker()() as db:
        resolved = sessions.resolve(db, session_token)
        if resolved.user is None:
            raise SessionInvalid(f"MediBridge session is not valid ({resolved.reason}); sign in again.")
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise SessionInvalid("The bound workflow run does not exist.")
        return RunContext(run.id, run.encounter_id, run.patient_id, resolved.user.id, resolved.user.role.key)


def _make_handler(name: str, session_token: str, run_id: uuid.UUID):
    td = tools.TOOLS[name]
    fields = td.args.model_fields

    def handler(**kwargs: Any) -> dict:
        ctx = _context(session_token, run_id)
        args = {k: v for k, v in kwargs.items() if v is not None}
        audit.record_now(action="mcp.tool_call", category="access", outcome="allowed", actor_user_id=ctx.actor_user_id,
                         actor_role=ctx.actor_role, patient_id=ctx.patient_id, encounter_id=ctx.encounter_id,
                         resource_type="tool", resource_id=name)
        try:
            result = tools.invoke(ctx, name, args, caller=f"mcp:{ctx.actor_user_id}")
        except tools.ToolDenied as exc:
            return {"error": exc.code, "message": str(exc)}
        return result if isinstance(result, dict) else {"items": result}

    params = [inspect.Parameter(fname, inspect.Parameter.KEYWORD_ONLY, annotation=f.annotation,
                                default=None if not f.is_required() else inspect.Parameter.empty)
              for fname, f in fields.items()]
    handler.__signature__ = inspect.Signature(params, return_annotation=dict)  # type: ignore[attr-defined]
    handler.__annotations__ = {**{p.name: p.annotation for p in params}, "return": dict}
    handler.__name__ = name.replace(".", "_")
    return handler


def build_server(session_token: str, run_id: uuid.UUID) -> MCPServer:
    server = MCPServer(name="medibridge", version="0.1.0",
                       instructions="Read-only, patient-scoped clinical tools for one MediBridge workflow run. "
                                    "Synthetic research/demo data only; not for clinical use.")
    for name, td in tools.TOOLS.items():
        if td.kind != "read":
            continue  # write tools are never exposed over MCP
        server.add_tool(_make_handler(name, session_token, run_id), name=name, description=td.description,
                        structured_output=False)
    return server


def main() -> None:
    token = os.environ.get("MEDIBRIDGE_MCP_SESSION")
    run = os.environ.get("MEDIBRIDGE_MCP_RUN_ID")
    if not token or not run:
        raise SystemExit("Set MEDIBRIDGE_MCP_SESSION and MEDIBRIDGE_MCP_RUN_ID.")
    _context(token, uuid.UUID(run))  # fail fast on an invalid session or run
    build_server(token, uuid.UUID(run)).run("stdio")
