"""Medication knowledge (reference data) and the per-patient medication-safety screen."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..security.deps import Principal, ensure_patient_scope, require
from ..services import audit, knowledge

router = APIRouter(prefix="/api", tags=["knowledge"])


def _kg():
    try:
        return knowledge.safe_graph()
    except knowledge.KnowledgeUnavailable:
        raise HTTPException(503, detail={"code": "knowledge_unavailable",
                                         "message": "The medication knowledge graph is unavailable; screening was NOT performed."})


@router.get("/knowledge/release")
def release(principal: Principal = Depends(require("medication.read"))) -> dict:
    kg = _kg()
    r = kg.release or {}
    return {"id": r.get("id"), "built_at": r.get("built_at"), "sources": r.get("sources"), "counts": r.get("counts"),
            "curated_status": r.get("curated_status")}


@router.get("/knowledge/drugs")
def lookup(q: str = Query(..., min_length=2, max_length=120), principal: Principal = Depends(require("medication.read"))) -> dict:
    return _kg().lookup_drug(q)


@router.get("/patients/{patient_id}/medication-safety")
def patient_screen(patient_id: uuid.UUID, principal: Principal = Depends(require("medication.read")),
                   db: Session = Depends(get_db)) -> dict:
    ensure_patient_scope(db, principal, patient_id, "medication.read")
    inputs = knowledge.recorded_inputs(db, patient_id)
    kg = _kg()
    result = kg.screen(inputs["medications"], inputs["allergies"], inputs["conditions"], inputs["context"])
    rx = [m["resolution"]["rxcui"] for m in result["medications"] if m["resolution"]["rxcui"]]
    result["graph"] = kg.neighborhood(rx) if rx else {"nodes": [], "edges": []}
    audit.record(db, action="medication.screen", category="safety", outcome="success", patient_id=patient_id,
                 resource_type="medication_screen",
                 details={"alerts": len(result["alerts"]), "release": result["release"],
                          "completeness": result["completeness"]["status"]}, **principal.audit_ctx())
    db.commit()
    return result
