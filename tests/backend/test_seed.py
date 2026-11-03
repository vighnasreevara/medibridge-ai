"""Synthea cardiology cohort invariants: volumes, scenarios, cardiac-only scope and provenance."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from medibridge_api.models import (Condition, Encounter, MedicationStatement, Observation, Patient, Procedure)
from medibridge_seed import cohort, synthea
from medibridge_seed.catalog import CONDITION_DROP, CONDITION_KEEP, LABS, PROCEDURE_KEEP, VITALS
from tests.conftest import make_user

pytestmark = pytest.mark.skipif(not (synthea.OUTPUT / "fhir").exists(),
                                reason="run: uv run python -m medibridge_seed.synthea generate")


@pytest.fixture(scope="module")
def seeded(test_database):
    from medibridge_api.db import get_sessionmaker

    with get_sessionmaker()() as db:
        doctors = [make_user(db, "doctor") for _ in range(4)]
        nurses = [make_user(db, "nurse") for _ in range(3)]
        before = db.scalar(select(func.count()).select_from(Patient))
        result = cohort.build(db, doctors, nurses, date(2026, 10, 22))
        db.commit()
        result["new_patients"] = db.scalar(select(func.count()).select_from(Patient)) - before
        result["ids"] = [p.id for p in db.scalars(select(Patient).where(Patient.registration_source == "synthea"))]
        return result


def test_minimum_volumes(seeded):
    assert seeded["new_patients"] >= 25
    assert seeded["counts"]["encounters"] >= 75
    assert seeded["counts"]["documents"] >= 100
    assert seeded["counts"]["observations"] >= 1000
    assert seeded["source"]["generator"].startswith("Synthea")


def test_all_named_scenarios_present(seeded):
    keys = {s["key"] for s in seeded["scenarios"]}
    assert {"af_anticoagulant_nsaid", "digoxin_interaction", "statin_macrolide", "chest_pain_red_flag", "nitrate_pde5",
            "heart_failure_nsaid", "ace_inhibitor_allergy", "uncertain_medication", "new_patient_registration",
            "failed_document_ingestion", "prompt_injection_document", "identifiers_in_document", "missing_document",
            "contradictory_allergy_records", "interrupted_execution", "multi_encounter_timeline"} <= keys
    new = next(s for s in seeded["scenarios"] if s["key"] == "new_patient_registration")
    assert new["patient_id"] is None and new["registration"]["birth_date"]


def test_every_patient_is_synthetic_with_a_fictional_mrn(db, seeded):
    rows = db.scalars(select(Patient).where(Patient.id.in_(seeded["ids"]))).all()
    assert rows and all(p.is_synthetic and p.mrn.startswith("MB") and not any(c.isdigit() for c in p.family_name)
                        for p in rows)


def test_only_cardiology_conditions_are_imported(db, seeded):
    names = db.scalars(select(Condition.name).where(Condition.patient_id.in_(seeded["ids"]))).all()
    assert names and all(CONDITION_KEEP.search(n) and not CONDITION_DROP.search(n) for n in names)


def test_only_cardiology_results_and_procedures_are_imported(db, seeded):
    codes = set(db.scalars(select(Observation.code).where(Observation.patient_id.in_(seeded["ids"]))))
    assert codes and codes <= set(LABS) | set(VITALS)
    procs = db.scalars(select(Procedure.name).where(Procedure.patient_id.in_(seeded["ids"]))).all()
    assert procs and all(PROCEDURE_KEEP.search(n) for n in procs)


def test_medications_carry_dose_and_frequency_from_the_source(db, seeded):
    meds = db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id.in_(seeded["ids"]),
                                                         MedicationStatement.med_status == "active")).all()
    # The strength comes from the product; the frequency only from the order's dosing instructions. Synthea's cardiac
    # module writes many chronic orders without instructions, and then the frequency stays empty (never assumed).
    assert sum(1 for m in meds if m.dose_text) >= len(meds) * 0.8
    assert {m.frequency_text for m in meds} <= {None, "once daily", "twice daily", "three times daily",
                                                "four times daily", "once weekly", "as needed"}
    assert all(m.source_ref.startswith(("synthea:", "scenario-augmentation:")) for m in meds)


def test_scenario_facts_are_from_the_record_or_marked_as_augmentation(db, seeded):
    s = next(x for x in seeded["scenarios"] if x["key"] == "af_anticoagulant_nsaid")
    warfarin = db.scalars(select(MedicationStatement).where(MedicationStatement.patient_id == s["patient_id"],
                                                             MedicationStatement.medication_text.ilike("%warfarin%"))).all()
    assert warfarin and all(m.source_ref.startswith(("synthea:", "scenario-augmentation:af_anticoagulant_nsaid"))
                            for m in warfarin)


def test_every_encounter_has_a_patient(db, seeded):
    assert db.scalar(select(func.count()).select_from(Encounter).where(Encounter.patient_id.is_(None))) == 0
