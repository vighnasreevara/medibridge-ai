"""Patient Registry (EMPI) API: find a returning patient or register a new one, via the Patient Registry agent.

The API calls the agent over A2A with a capability bound to the signed-in user only (there is no workflow run
yet). Results carry minimal demographics; registering adds the user (and the chosen attending doctor) to the
patient's care team so the intake can start.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..security.deps import Principal, require

router = APIRouter(prefix="/api/registry", tags=["registry"])


class MatchIn(BaseModel):
    mrn: str | None = Field(None, max_length=20)
    given_name: str = Field("", max_length=80)
    family_name: str = Field("", max_length=80)
    birth_date: date


class RegisterIn(BaseModel):
    given_name: str = Field(min_length=1, max_length=80)
    family_name: str = Field(min_length=2, max_length=80)
    birth_date: date
    sex_at_birth: Literal["female", "male", "intersex", "unknown"]
    attending_user_id: uuid.UUID | None = None
    confirm_new: bool = False


def _call(principal: Principal, request: dict) -> dict:
    from medibridge_agents.a2a import client
    from medibridge_agents.a2a.registry import REMOTE_AGENTS

    from ..config import get_settings

    skill = REMOTE_AGENTS["patient_registry"].skill("match_or_register_patient")
    state = {"actor_user_id": str(principal.user_id), "actor_role": principal.role, "registration": request}
    if get_settings().agent_transport == "inprocess":
        return skill.fn(state)["registry"]
    try:
        return client.call(skill, state).delta["registry"]
    except client.AgentUnavailable as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": "The Patient Registry agent is not reachable."})
    except client.AgentRefused as exc:
        raise HTTPException(502, detail={"code": exc.code, "message": "The Patient Registry agent refused the request."})


@router.post("/match")
def match(body: MatchIn, principal: Principal = Depends(require("registry.search"))) -> dict:
    if not body.mrn and (not body.given_name.strip() or len(body.family_name.strip()) < 2):
        raise HTTPException(422, detail={"code": "name_or_mrn_required"})
    return _call(principal, {"mode": "match", **body.model_dump(mode="json")})


@router.post("/register", status_code=201)
def register(body: RegisterIn, principal: Principal = Depends(require("registry.register"))) -> dict:
    result = _call(principal, {"mode": "register", **body.model_dump(mode="json")})
    if result["status"] == "possible_duplicates":
        raise HTTPException(409, detail={"code": "possible_duplicates", "candidates": result["candidates"]})
    if result["status"] != "registered":
        raise HTTPException(422, detail={"code": result.get("reason") or result["status"]})
    return result
