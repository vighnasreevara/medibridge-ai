"""Server-Sent Events stream of workflow events (mission-control live-event protocol).

Frames: ``hello`` (cursor info), ``event`` (one workflow event, ``id`` = seq), ``heartbeat`` every 15 s,
``session_ended`` when the session is no longer valid. Clients resume with ``Last-Event-ID``.
Events are witnesses: the client uses them to invalidate queries, never as state.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db, get_sessionmaker
from ..security import sessions
from ..security.deps import Principal, require
from ..services import events

router = APIRouter(prefix="/api/events", tags=["events"])

POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15
REVALIDATE_SECONDS = 30
REORDER_WINDOW = 200  # re-scan this many seqs behind the cursor: identity values can commit out of order


def _scope(db: Session, principal: Principal):
    return None if principal.role == "admin" else events.visible_patient_ids(db, principal.user_id)


@router.get("")
def backfill(after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), latest: int | None = Query(None, ge=1, le=1000),
             types: str | None = Query(None, max_length=400),
             principal: Principal = Depends(require("agents.monitor")), db: Session = Depends(get_db)) -> dict:
    """``after``: events after a cursor (replay). ``latest``: the most recent N events, newest first."""
    scope = _scope(db, principal)
    if latest:
        from sqlalchemy import select

        from ..models import WorkflowEvent

        stmt = select(WorkflowEvent).order_by(WorkflowEvent.seq.desc()).limit(latest)
        if scope is not None:
            stmt = stmt.where(WorkflowEvent.patient_id.in_(scope) | WorkflowEvent.patient_id.is_(None))
        if types:
            stmt = stmt.where(WorkflowEvent.type.in_(types.split(",")))
        rows = list(db.scalars(stmt))
    else:
        rows = events.fetch(db, after=after, limit=limit, scope=scope)
    return {"events": [events.serialize(e) for e in rows], "last_seq": events.last_seq(db)}


def _frame(event: str, data: dict, event_id: int | None = None) -> bytes:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/stream")
async def stream(request: Request, after: int | None = Query(None, ge=0),
                 principal: Principal = Depends(require("agents.monitor"))) -> StreamingResponse:
    token = request.cookies.get(sessions.COOKIE_NAME)
    header_cursor = request.headers.get("last-event-id")
    cursor = int(header_cursor) if header_cursor and header_cursor.isdigit() else after

    def load(cursor_value: int | None, scope_cache: dict) -> tuple[list[dict], int, bool]:
        with get_sessionmaker()() as db:
            now = time.monotonic()
            if now - scope_cache.get("at", 0) > REVALIDATE_SECONDS:
                if sessions.resolve(db, token).user is None:
                    return [], 0, False
                scope_cache.update(at=now, scope=_scope(db, principal))
            head = events.last_seq(db)
            start = head if cursor_value is None else max(cursor_value - REORDER_WINDOW, 0)
            rows = events.fetch(db, after=start, limit=500, scope=scope_cache["scope"])
            return [events.serialize(e) for e in rows], head, True

    async def body():
        scope_cache: dict = {}
        sent: deque[int] = deque(maxlen=5000)
        sent_set: set[int] = set()
        rows, head, ok = await asyncio.to_thread(load, cursor, scope_cache)
        yield b"retry: 2000\n\n"
        yield _frame("hello", {"last_seq": head, "replay_from": cursor, "server_time": time.time()})
        floor = cursor if cursor is not None else head  # the client already has everything up to here
        position = floor
        last_beat = time.monotonic()
        while ok:
            for ev in rows:
                if ev["seq"] in sent_set or ev["seq"] <= floor:
                    continue
                if len(sent) == sent.maxlen:
                    sent_set.discard(sent[0])
                sent.append(ev["seq"])
                sent_set.add(ev["seq"])
                position = max(position or 0, ev["seq"])
                yield _frame("event", ev, ev["seq"])
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                last_beat = time.monotonic()
                yield _frame("heartbeat", {"last_seq": head, "server_time": time.time()})
            if await request.is_disconnected():
                return
            await asyncio.sleep(POLL_SECONDS)
            rows, head, ok = await asyncio.to_thread(load, position, scope_cache)
        yield _frame("session_ended", {"reason": "session_invalid"})

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
