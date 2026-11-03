"""Workflow state (checkpointed in Postgres schema ``lg``). Values are plain JSON-serializable dicts.

Large clinical content (audio, transcripts, documents) stays in its own tables; the state holds ids,
structured findings and the evidence bundle the handoff is built from.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class IntakeState(TypedDict, total=False):
    # identity of the run (set by the runner; never by a model)
    run_id: str
    encounter_id: str
    patient_id: str
    actor_user_id: str
    actor_role: str
    steps: int
    # agent outputs
    identity: dict
    transcription_id: str
    transcript: dict          # reviewed transcript summary: {segments:[{id,speaker,text}], reviewed_by, reviewed_at}
    findings: dict            # NER output after validation + normalization
    escalations: list
    history: dict
    documents: dict
    medication_safety: dict
    handoff: dict             # {handoff_id, version, content_sha256, status}
    review: dict              # doctor decision at the handoff gate
    documentation: dict       # note draft, signed orders, safety screen and decision, commit result
    avs: dict                 # after-visit summary draft for the patient
    # control
    outcome: dict             # {status: failed|blocked|completed|rejected, node, code}
    notices: Annotated[list[Any], operator.add]
