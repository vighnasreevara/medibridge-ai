"""Run lifecycle: start, resume at gates, recover interrupted runs. One active execution per run."""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import select

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import AgentExecution, WorkflowRun
from medibridge_api.models.base import utcnow
from medibridge_api.services import events

from .graph import build_graph

log = logging.getLogger("medibridge.runner")
RECURSION_LIMIT = 80
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="medibridge-run")
_active: set[uuid.UUID] = set()
_active_lock = threading.Lock()


class RunBusy(Exception):
    code = "run_busy"


def _conn_string() -> str:
    from medibridge_api.config import get_settings

    return get_settings().checkpoint_conn_string("app")


@lru_cache
def graph():
    pool = ConnectionPool(_conn_string(), max_size=6, kwargs={"autocommit": True, "prepare_threshold": 0,
                                                             "row_factory": dict_row}, open=True)
    import atexit

    atexit.register(pool.close)
    return build_graph().compile(checkpointer=PostgresSaver(pool))


def _config(run: WorkflowRun) -> dict:
    return {"configurable": {"thread_id": run.langgraph_thread_id}, "recursion_limit": RECURSION_LIMIT}


def _execute(run_id: uuid.UUID, payload: Any) -> None:
    try:
        with get_sessionmaker()() as db:
            run = db.get(WorkflowRun, run_id)
            config = _config(run)
        graph().invoke(payload, config)
    except Exception as exc:  # noqa: BLE001 - never leave a run silently "running"
        log.exception("run %s crashed", run_id)
        with get_sessionmaker()() as db:
            run = db.get(WorkflowRun, run_id)
            run.status, run.error_code, run.ended_at = "failed", f"runner:{type(exc).__name__}", utcnow()
            events.emit(db, "run.failed", run_id=run.id, encounter_id=run.encounter_id, patient_id=run.patient_id,
                        agent_id="supervisor", node=run.current_node, status="failed", error_code="runner_exception")
            db.commit()
    finally:
        with _active_lock:
            _active.discard(run_id)


def _submit(run_id: uuid.UUID, payload: Any, wait: bool = False) -> Future:
    with _active_lock:
        if run_id in _active:
            raise RunBusy("This workflow is already executing; wait for it to pause or finish.")
        _active.add(run_id)
    future = _executor.submit(_execute, run_id, payload)
    if wait:
        future.result()
    return future


def is_active(run_id: uuid.UUID) -> bool:
    with _active_lock:
        return run_id in _active


def start(run: WorkflowRun, transcription_id: uuid.UUID, wait: bool = False) -> Future:
    state = {"run_id": str(run.id), "encounter_id": str(run.encounter_id), "patient_id": str(run.patient_id),
             "actor_user_id": str(run.started_by), "actor_role": "nurse", "steps": 0,
             "transcription_id": str(transcription_id), "notices": []}
    with get_sessionmaker()() as db:
        from medibridge_api.models import User

        state["actor_role"] = db.get(User, run.started_by).role.key
    return _submit(run.id, state, wait)


def resume(run_id: uuid.UUID, payload: dict, wait: bool = False) -> Future:
    return _submit(run_id, Command(resume=payload), wait)


def continue_interrupted(run_id: uuid.UUID, wait: bool = False) -> Future:
    """Resume an interrupted run from its last checkpoint (the node that was running re-executes)."""
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, run_id)
        run.status = "running"
        events.emit(db, "run.resumed", run_id=run.id, encounter_id=run.encounter_id, patient_id=run.patient_id,
                    agent_id="supervisor", node=run.current_node, status="running")
        db.commit()
    return _submit(run_id, None, wait)


def recover_on_startup() -> int:
    """Runs that were executing when the process stopped are marked interrupted (resumable)."""
    count = 0
    with get_sessionmaker()() as db:
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.status.in_(("running", "queued")))):
            if is_active(run.id):
                continue
            run.status, run.error_code = "interrupted", "process_restart"
            # Executions that were in flight died with the process: close them so monitoring shows the truth.
            for ex in db.scalars(select(AgentExecution).where(AgentExecution.run_id == run.id,
                                                              AgentExecution.status == "running")):
                ex.status, ex.error_code, ex.ended_at = "failed", "process_restart", utcnow()
            events.emit(db, "run.interrupted", run_id=run.id, encounter_id=run.encounter_id, patient_id=run.patient_id,
                        agent_id="supervisor", node=run.current_node, status="interrupted")
            count += 1
        db.commit()
    return count


def pending_gate(run_id: uuid.UUID) -> dict | None:
    with get_sessionmaker()() as db:
        run = db.get(WorkflowRun, run_id)
        snap = graph().get_state(_config(run))
    for task in snap.tasks:
        for intr in task.interrupts:
            return intr.value
    return None
