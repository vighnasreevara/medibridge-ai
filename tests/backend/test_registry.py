"""Patient Registry (EMPI) through the API and the Patient Registry agent (A2A, in-process transport)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from medibridge_api.models import AuditLog, CareTeamAssignment, Patient
from tests.conftest import login


def test_returning_patient_matches_by_mrn_and_date_of_birth(client, user_factory, patient_factory):
    p = patient_factory(birth_date=date(1959, 3, 14))
    login(client, user_factory("nurse"))
    r = client.post("/api/registry/match", json={"mrn": p.mrn, "birth_date": "1959-03-14"})
    assert r.status_code == 200 and r.json()["status"] == "matched"
    assert r.json()["patient"]["mrn"] == p.mrn and set(r.json()["patient"]) == {
        "patient_id", "mrn", "given_name", "family_name", "birth_date", "sex_at_birth"}


def test_mrn_with_the_wrong_date_of_birth_is_refused_and_audited(client, db, user_factory, patient_factory):
    p = patient_factory(birth_date=date(1959, 3, 14))
    login(client, user_factory("nurse"))
    r = client.post("/api/registry/match", json={"mrn": p.mrn, "birth_date": "1960-03-14"})
    assert r.json() == {"status": "identity_mismatch", "candidates": []}
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "registry.match").order_by(AuditLog.seq.desc())).first()
    assert entry.outcome == "denied" and entry.details["reason"] == "dob_mismatch"


def test_name_and_date_of_birth_return_ranked_candidates(client, user_factory, patient_factory):
    p = patient_factory(given_name="Rosalind", family_name="Pemberton", birth_date=date(1950, 1, 2))
    login(client, user_factory("nurse"))
    r = client.post("/api/registry/match", json={"given_name": "Rosalin", "family_name": "Pembertn", "birth_date": "1950-01-02"})
    assert r.json()["status"] == "candidates" and r.json()["candidates"][0]["patient_id"] == str(p.id)


def test_registering_a_new_patient_puts_the_user_on_the_care_team(client, db, user_factory):
    nurse, doctor = user_factory("nurse"), user_factory("doctor")
    login(client, nurse)
    r = client.post("/api/registry/register", json={"given_name": "Corwin", "family_name": "Allendale-Test",
                                                    "birth_date": "1968-04-17", "sex_at_birth": "male",
                                                    "attending_user_id": str(doctor.id)})
    assert r.status_code == 201, r.text
    pid = r.json()["patient"]["patient_id"]
    p = db.get(Patient, uuid.UUID(pid))
    assert p.registration_source == "registration_desk" and p.registered_by == nurse.id and p.mrn.startswith("MB")
    team = {(a.user_id, a.role) for a in db.scalars(select(CareTeamAssignment).where(CareTeamAssignment.patient_id == p.id))}
    assert {(nurse.id, "intake"), (doctor.id, "attending")} <= team
    # ... so the intake can start: the nurse can create today's encounter.
    enc = client.post("/api/encounters", json={"patient_id": pid, "attending_user_id": str(doctor.id),
                                               "chief_complaint": "Palpitations"})
    assert enc.status_code == 201, enc.text


def test_likely_duplicates_are_shown_before_creating_a_record(client, user_factory, patient_factory):
    existing = patient_factory(given_name="Imelda", family_name="Brightwater", birth_date=date(1944, 7, 9))
    login(client, user_factory("nurse"))
    body = {"given_name": "Imelda", "family_name": "Brightwatter", "birth_date": "1944-07-09", "sex_at_birth": "female"}
    r = client.post("/api/registry/register", json=body)
    assert r.status_code == 409 and r.json()["detail"]["candidates"][0]["patient_id"] == str(existing.id)
    confirmed = client.post("/api/registry/register", json={**body, "confirm_new": True})
    assert confirmed.status_code == 201


def test_administrators_can_search_but_not_register(client, user_factory):
    login(client, user_factory("admin"))
    assert client.post("/api/registry/register", json={"given_name": "A", "family_name": "Bcd", "birth_date": "1970-01-01",
                                                       "sex_at_birth": "female"}).status_code == 403
