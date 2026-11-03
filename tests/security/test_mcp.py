"""MCP server: read-only surface, session re-validation and server-side patient binding."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import update

from medibridge_api.models import AuthSession, Encounter, WorkflowRun
from medibridge_api.models.base import utcnow
from medibridge_api.security import sessions
from tests.conftest import assign, login


@pytest.fixture
def bound(client, db, user_factory, patient_factory):
    nurse, patient = user_factory("nurse"), patient_factory()
    assign(db, nurse, patient)
    enc = Encounter(patient_id=patient.id, status="in_progress", created_by=nurse.id)
    db.add(enc)
    db.flush()
    run = WorkflowRun(encounter_id=enc.id, patient_id=patient.id, status="running", idempotency_key=uuid.uuid4().hex,
                      langgraph_thread_id="t", started_by=nurse.id)
    db.add(run)
    db.commit()
    login(client, nurse)
    return {"token": client.cookies.get(sessions.COOKIE_NAME), "run": run, "patient": patient}


def _call(server, name, args):
    result = asyncio.run(server.call_tool(name, args))
    blocks = getattr(result, "content", result)
    return json.loads(blocks[0].text)


def test_only_read_tools_are_exposed(bound):
    from medibridge_mcp.server import build_server

    names = {t.name for t in asyncio.run(build_server(bound["token"], bound["run"].id).list_tools())}
    assert "patient.lookup" in names and "document.search" in names
    assert not {"handoff.create_draft", "documentation.commit_approved", "documentation.prepare_update"} & names


def test_patient_comes_from_the_bound_run(bound):
    from medibridge_mcp.server import build_server

    out = _call(build_server(bound["token"], bound["run"].id), "patient.lookup", {})
    assert out["patient_id"] == str(bound["patient"].id) and "mrn" in out


def test_client_supplied_patient_id_is_rejected(bound, patient_factory):
    from medibridge_mcp.server import build_server

    other = patient_factory()
    out = _call(build_server(bound["token"], bound["run"].id), "patient.get_history", {"patient_id": str(other.id)})
    assert out["error"] == "patient_id_mismatch"


def test_expired_session_is_refused_on_every_call(bound, db):
    from medibridge_mcp.server import build_server

    server = build_server(bound["token"], bound["run"].id)
    db.execute(update(AuthSession).where(AuthSession.id == sessions.digest(bound["token"]))
               .values(last_seen_at=utcnow() - timedelta(hours=3)))
    db.commit()
    with pytest.raises(Exception, match="not valid|Error executing tool"):
        _call(server, "patient.lookup", {})
