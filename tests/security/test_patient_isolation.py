"""Patient scope, data minimization and access auditing (guardrails 2 and 3)."""
from __future__ import annotations

from sqlalchemy import select

from medibridge_api.models import AuditLog, MedicationStatement, PatientContact
from tests.conftest import assign, login


def _setup(db, user_factory, patient_factory):
    nurse = user_factory("nurse")
    mine, other = patient_factory(), patient_factory()
    assign(db, nurse, mine, "nursing")
    db.add(PatientContact(patient_id=mine.id, kind="phone", value="+1-555-0199"))
    db.add(MedicationStatement(patient_id=other.id, medication_text="secret-med 5 mg", source_kind="clinician_documented",
                               med_status="active"))
    db.commit()
    return nurse, mine, other


CLINICAL_PATHS = ("", "/timeline", "/conditions", "/medications", "/allergies", "/notes", "/documents", "/vitals",
                  "/symptoms")


def test_clinician_reads_assigned_patient(client, db, user_factory, patient_factory):
    nurse, mine, _ = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    for path in CLINICAL_PATHS:
        assert client.get(f"/api/patients/{mine.id}{path}").status_code == 200, path


def test_clinician_cannot_read_unassigned_patient_and_denial_is_audited(client, db, user_factory, patient_factory):
    nurse, _, other = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    for path in CLINICAL_PATHS:
        resp = client.get(f"/api/patients/{other.id}{path}")
        assert resp.status_code == 404, path  # 404, not 403: does not confirm the record exists
        assert "secret-med" not in resp.text
    denials = db.scalars(select(AuditLog).where(AuditLog.action == "access.denied", AuditLog.patient_id == other.id,
                                                AuditLog.actor_user_id == nurse.id)).all()
    assert len(denials) == len(CLINICAL_PATHS)
    assert all(d.details.get("reason") == "no_care_team_assignment" for d in denials)


def test_successful_record_access_is_audited(client, db, user_factory, patient_factory):
    nurse, mine, _ = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    client.get(f"/api/patients/{mine.id}/medications")
    row = db.scalars(select(AuditLog).where(AuditLog.action == "patient.read", AuditLog.patient_id == mine.id,
                                            AuditLog.resource_type == "medications")).first()
    assert row is not None and row.outcome == "allowed"


def test_admin_has_no_clinical_access(client, db, user_factory, patient_factory):
    admin = user_factory("admin")
    _, mine, _ = _setup(db, user_factory, patient_factory)
    assign(db, admin, mine, "consulting")  # even with an assignment, the role lacks the permission
    login(client, admin)
    assert client.get(f"/api/patients/{mine.id}").status_code == 403
    assert client.get(f"/api/patients/{mine.id}/medications").status_code == 403


def test_registry_search_returns_minimal_demographics_only(client, db, user_factory, patient_factory):
    nurse, mine, _ = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    items = client.get("/api/patients", params={"q": mine.mrn}).json()["items"]
    assert len(items) == 1
    assert set(items[0]) == {"id", "display_name", "mrn", "birth_date", "age", "sex_at_birth", "in_care_team",
                             "is_synthetic"}
    assert "555" not in str(items)
    log = db.scalars(select(AuditLog).where(AuditLog.action == "registry.search",
                                            AuditLog.actor_user_id == nurse.id)).first()
    assert mine.mrn not in str(log.details)  # the query itself is not logged


def test_contacts_never_appear_in_clinical_views(client, db, user_factory, patient_factory):
    nurse, mine, _ = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    for path in CLINICAL_PATHS:
        assert "+1-555-0199" not in client.get(f"/api/patients/{mine.id}{path}").text


def test_admin_identifiers_require_admin_and_reason(client, db, user_factory, patient_factory):
    nurse, mine, _ = _setup(db, user_factory, patient_factory)
    login(client, nurse)
    assert client.get(f"/api/patients/{mine.id}/admin-identifiers", params={"reason": "billing query"}).status_code == 403
    admin = user_factory("admin")
    login(client, admin)
    assert client.get(f"/api/patients/{mine.id}/admin-identifiers").status_code == 422  # reason is mandatory
    resp = client.get(f"/api/patients/{mine.id}/admin-identifiers", params={"reason": "billing query"})
    assert resp.status_code == 200 and resp.json()["contacts"][0]["value"] == "+1-555-0199"


def test_worklist_only_lists_care_team_patients(client, db, user_factory, patient_factory):
    doctor, nurse = user_factory("doctor"), user_factory("nurse")
    mine, other = patient_factory(), patient_factory()
    login(client, nurse)
    for p in (mine, other):
        assert client.post("/api/encounters", json={"patient_id": str(p.id), "attending_user_id": str(doctor.id),
                                                    "chief_complaint": "Cough"}).status_code == 201
    second_nurse = user_factory("nurse")
    assign(db, second_nurse, mine, "nursing")
    login(client, second_nurse)
    ids = {item["patient_id"] for item in client.get("/api/encounters", params={"scope": "all"}).json()["items"]}
    assert str(mine.id) in ids and str(other.id) not in ids
