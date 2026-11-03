"""Patient Registry (EMPI): find a returning patient or register a new one.

Matching rules (deterministic; no model):
- MRN + date of birth: exact match only. An MRN whose date of birth differs is refused and audited (wrong
  wristband or wrong patient), never "closest match";
- name + date of birth: same date of birth and similar names (family and given name similarity), ranked;
- registration: given name, family name, date of birth and sex are required. Likely duplicates (same date of birth,
  similar name) are returned instead of creating a record unless the user confirms after reviewing them.
Results carry minimal demographics only (MRN, name, date of birth, sex). The registering user and the attending
doctor join the care team; every search and registration is audited.
"""
from __future__ import annotations

import difflib
import uuid
from datetime import date

from sqlalchemy import func, select

from medibridge_api.db import get_sessionmaker
from medibridge_api.models import CareTeamAssignment, Patient, User
from medibridge_api.security.rbac import role_has
from medibridge_api.services import audit

NAME_MATCH = 0.82


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _view(p: Patient, score: float | None = None) -> dict:
    return {"patient_id": str(p.id), "mrn": p.mrn, "given_name": p.given_name, "family_name": p.family_name,
            "birth_date": p.birth_date.isoformat(), "sex_at_birth": p.sex_at_birth,
            **({"score": round(score, 2)} if score is not None else {})}


def _candidates(db, given: str, family: str, dob: date) -> list[dict]:
    rows = db.scalars(select(Patient).where(Patient.birth_date == dob, Patient.deleted_at.is_(None))).all()
    out = []
    for p in rows:
        score = 0.6 * _similar(family, p.family_name) + 0.4 * _similar(given, p.given_name)
        if score >= NAME_MATCH or (_similar(family, p.family_name) >= 0.9 and given[:1].lower() == p.given_name[:1].lower()):
            out.append(_view(p, score))
    return sorted(out, key=lambda c: -c["score"])[:5]


REGISTRATION_RANGE = 7_000_000  # desk registrations get their own MRN range (imported records use MB1xxxxx)


def _next_mrn(db) -> str:
    count = db.scalar(select(func.count()).select_from(Patient).where(Patient.registration_source == "registration_desk"))
    number = REGISTRATION_RANGE + (count or 0) + 1
    while db.scalar(select(func.count()).select_from(Patient).where(Patient.mrn == f"MB{number}")):
        number += 1
    return f"MB{number}"


def match_or_register(state: dict) -> dict:
    actor = uuid.UUID(state["actor_user_id"])
    role = state["actor_role"]
    req = state.get("registration") or {}
    mode = req.get("mode", "match")
    if not role_has(role, "registry.search") or (mode == "register" and not role_has(role, "registry.register")):
        return {"registry": {"status": "denied", "reason": "permission_missing"}}
    with get_sessionmaker()() as db:
        audit_ctx = {"actor_user_id": actor, "actor_role": role}
        try:
            dob = date.fromisoformat(str(req.get("birth_date")))
        except ValueError:
            return {"registry": {"status": "invalid", "reason": "birth_date_required"}}
        given, family = str(req.get("given_name") or "").strip(), str(req.get("family_name") or "").strip()

        if mode == "match":
            mrn = str(req.get("mrn") or "").strip().upper()
            if mrn:
                p = db.scalar(select(Patient).where(Patient.mrn == mrn, Patient.deleted_at.is_(None)))
                if p is None or p.birth_date != dob:
                    audit.record(db, action="registry.match", category="safety", outcome="denied",
                                 resource_type="patient", resource_id=mrn,
                                 details={"reason": "mrn_not_found" if p is None else "dob_mismatch"}, **audit_ctx)
                    db.commit()
                    return {"registry": {"status": "no_match" if p is None else "identity_mismatch", "candidates": []}}
                audit.record(db, action="registry.match", category="access", outcome="allowed", patient_id=p.id,
                             resource_type="patient", resource_id=p.id, details={"method": "mrn+dob"}, **audit_ctx)
                db.commit()
                return {"registry": {"status": "matched", "method": "mrn+dob", "patient": _view(p)}}
            if len(given) < 1 or len(family) < 2:
                return {"registry": {"status": "invalid", "reason": "name_or_mrn_required"}}
            found = _candidates(db, given, family, dob)
            audit.record(db, action="registry.search", category="access", outcome="allowed", resource_type="patient",
                         details={"method": "name+dob", "candidates": len(found)}, **audit_ctx)
            db.commit()
            return {"registry": {"status": "candidates" if found else "no_match", "method": "name+dob",
                                 "candidates": found}}

        # ---- register
        sex = req.get("sex_at_birth")
        if len(given) < 1 or len(family) < 2 or sex not in ("female", "male", "intersex", "unknown"):
            return {"registry": {"status": "invalid", "reason": "given_family_dob_sex_required"}}
        if dob > date.today() or dob.year < 1900:
            return {"registry": {"status": "invalid", "reason": "birth_date_out_of_range"}}
        dupes = _candidates(db, given, family, dob)
        if dupes and not req.get("confirm_new"):
            return {"registry": {"status": "possible_duplicates", "candidates": dupes}}
        attending = None
        if req.get("attending_user_id"):
            attending = db.get(User, uuid.UUID(str(req["attending_user_id"])))
            if attending is None or attending.status != "active" or attending.role.key != "doctor":
                return {"registry": {"status": "invalid", "reason": "attending_must_be_active_doctor"}}
        p = Patient(id=uuid.uuid4(), mrn=_next_mrn(db), given_name=given[:80], family_name=family[:80], birth_date=dob,
                    sex_at_birth=sex, registration_source="registration_desk", registered_by=actor)
        db.add(p)
        db.flush()
        db.add(CareTeamAssignment(patient_id=p.id, user_id=actor, role="intake", reason="registered the patient",
                                  granted_by=actor))
        if attending and attending.id != actor:
            db.add(CareTeamAssignment(patient_id=p.id, user_id=attending.id, role="attending",
                                      reason="attending at registration", granted_by=actor))
        audit.record(db, action="patient.registered", category="modification", outcome="success", patient_id=p.id,
                     resource_type="patient", resource_id=p.id,
                     details={"duplicates_reviewed": len(dupes), "attending_assigned": bool(attending)}, **audit_ctx)
        db.commit()
        return {"registry": {"status": "registered", "patient": _view(p), "duplicates_reviewed": len(dupes)}}
