"""Encounter creation, identity verification (guardrail 1) and consent gating."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from medibridge_api.models import AuditLog, CareTeamAssignment, Encounter
from medibridge_api.routes.encounters import require_ready_for_intake
from tests.conftest import login


@pytest.fixture
def started(client, db, user_factory, patient_factory):
    doctor, nurse = user_factory("doctor"), user_factory("nurse")
    patient = patient_factory(birth_date=date(1971, 4, 9))
    login(client, nurse)
    resp = client.post("/api/encounters", json={"patient_id": str(patient.id), "attending_user_id": str(doctor.id),
                                                "chief_complaint": "Cough", "encounter_class": "ambulatory"})
    assert resp.status_code == 201, resp.text
    return {"doctor": doctor, "nurse": nurse, "patient": patient, "encounter": resp.json()}


def test_creating_an_encounter_grants_care_team_scope(db, started):
    rows = db.scalars(select(CareTeamAssignment).where(CareTeamAssignment.patient_id == started["patient"].id)).all()
    assert {(r.user_id, r.role) for r in rows} == {(started["nurse"].id, "intake"), (started["doctor"].id, "attending")}
    assert started["encounter"]["status"] == "planned"
    assert started["encounter"]["identity_verified"] is False


def test_attending_must_be_a_doctor(client, user_factory, patient_factory):
    nurse = user_factory("nurse")
    login(client, nurse)
    resp = client.post("/api/encounters", json={"patient_id": str(patient_factory().id),
                                                "attending_user_id": str(nurse.id)})
    assert resp.status_code == 422


def test_duplicate_open_encounter_is_refused(client, started):
    resp = client.post("/api/encounters", json={"patient_id": str(started["patient"].id),
                                                "attending_user_id": str(started["doctor"].id)})
    assert resp.status_code == 409
    assert resp.json()["detail"]["encounter_id"] == started["encounter"]["id"]


def test_identity_mismatch_blocks_and_is_audited(client, db, started):
    eid = started["encounter"]["id"]
    resp = client.post(f"/api/encounters/{eid}/verify-identity",
                       json={"method": "wristband+dob", "mrn": started["patient"].mrn, "birth_date": "1971-09-04"})
    assert resp.status_code == 409 and resp.json()["detail"]["dob_match"] is False
    assert db.scalars(select(AuditLog).where(AuditLog.action == "identity.mismatch",
                                             AuditLog.patient_id == started["patient"].id)).first() is not None
    db.expire_all()
    assert db.get(Encounter, eid).identity_verified_at is None


def test_identity_verification_with_two_identifiers(client, started):
    eid = started["encounter"]["id"]
    resp = client.post(f"/api/encounters/{eid}/verify-identity",
                       json={"method": "wristband+dob", "mrn": started["patient"].mrn.lower(), "birth_date": "1971-04-09"})
    assert resp.status_code == 200 and resp.json()["identity_verified"] is True


def test_intake_requires_identity_and_both_consents(client, db, started):
    eid = started["encounter"]["id"]
    enc = db.get(Encounter, eid)
    with pytest.raises(HTTPException) as exc:
        require_ready_for_intake(db, enc)
    assert exc.value.detail["code"] == "identity_not_verified"
    client.post(f"/api/encounters/{eid}/verify-identity",
                json={"method": "wristband+dob", "mrn": started["patient"].mrn, "birth_date": "1971-04-09"})
    client.post(f"/api/encounters/{eid}/consents",
                json={"consent_type": "audio_recording", "status": "granted", "method": "verbal_witnessed"})
    db.expire_all()
    with pytest.raises(HTTPException) as exc:
        require_ready_for_intake(db, db.get(Encounter, eid))
    assert exc.value.detail["missing"] == ["ai_processing"]
    client.post(f"/api/encounters/{eid}/consents",
                json={"consent_type": "ai_processing", "status": "granted", "method": "verbal_witnessed"})
    db.expire_all()
    require_ready_for_intake(db, db.get(Encounter, eid))
    # A later withdrawal wins.
    client.post(f"/api/encounters/{eid}/consents",
                json={"consent_type": "audio_recording", "status": "withdrawn", "method": "verbal_witnessed"})
    db.expire_all()
    with pytest.raises(HTTPException):
        require_ready_for_intake(db, db.get(Encounter, eid))
