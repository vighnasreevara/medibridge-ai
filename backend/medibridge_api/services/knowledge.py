"""Shared access to the Neo4j knowledge graph and the per-patient medication-safety screen."""
from __future__ import annotations

import uuid
from functools import lru_cache

from neo4j import GraphDatabase
from sqlalchemy import select
from sqlalchemy.orm import Session

from medibridge_knowledge.graph import KnowledgeGraph

from ..config import get_settings
from ..models import Allergy, Condition, MedicationStatement
from . import clinical_views as views


class KnowledgeUnavailable(Exception):
    code = "knowledge_unavailable"


@lru_cache
def graph() -> KnowledgeGraph:
    s = get_settings()
    driver = GraphDatabase.driver(s.neo4j_uri, auth=("neo4j", s.neo4j_password.get_secret_value()),
                                  connection_timeout=5, max_connection_lifetime=600)
    return KnowledgeGraph(driver)


def safe_graph() -> KnowledgeGraph:
    kg = graph()
    try:
        kg._ensure_index()
    except Exception as exc:  # noqa: BLE001 - Neo4j down or no active release
        graph.cache_clear()
        raise KnowledgeUnavailable(str(type(exc).__name__)) from exc
    return kg


def recorded_inputs(db: Session, patient_id: uuid.UUID) -> dict:
    """Recorded (not newly reported) medications, allergies and conditions in screening format."""
    meds = db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id == patient_id,
                                                         MedicationStatement.status != "entered_in_error")).all()
    current = [m for m in meds if views.medication_category(m) in ("current", "status_unknown")]
    reconciliation = ("never_reconciled" if not current else
                      "never_reconciled" if any(views.reconciliation_state(m) == "never_reconciled" for m in current) else
                      "stale" if any(views.reconciliation_state(m) == "stale" for m in current) else "recent")
    allergies = db.scalars(select(Allergy).where(Allergy.patient_id == patient_id,
                                                 Allergy.verification_status.notin_(("refuted", "entered_in_error")),
                                                 Allergy.status != "entered_in_error")).all()
    conditions = db.scalars(select(Condition).where(Condition.patient_id == patient_id,
                                                    Condition.status != "entered_in_error")).all()
    return {
        "medications": [{"text": m.medication_text, "ref": f"medication_statement:{m.id}", "source_kind": m.source_kind,
                         "certainty": m.certainty, "category": views.medication_category(m),
                         "reconciliation": views.reconciliation_state(m), "dose_text": m.dose_text,
                         "frequency_text": m.frequency_text} for m in current],
        "allergies": [{"substance": a.substance_text, "ref": f"allergy:{a.id}", "verification_status": a.verification_status,
                       "source_kind": a.source_kind} for a in allergies],
        "conditions": [{"name": c.name, "ref": f"condition:{c.id}", "clinical_status": c.clinical_status} for c in conditions],
        "context": {"reconciliation": reconciliation},
    }
