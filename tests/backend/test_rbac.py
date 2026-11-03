"""RBAC catalogue invariants."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from medibridge_api.models import Role
from medibridge_api.security import rbac


def test_admin_has_no_patient_scoped_permissions():
    scoped = {p.key for p in rbac.PERMISSIONS if p.patient_scoped}
    assert not scoped & rbac.ROLES["admin"]["permissions"]


def test_only_doctors_can_approve_or_prescribe():
    for perm in ("handoff.approve", "note.approve", "prescription.create"):
        holders = {role for role, spec in rbac.ROLES.items() if perm in spec["permissions"]}
        assert holders == {"doctor"}, perm


def test_nurses_cannot_read_administrative_identifiers():
    assert not rbac.role_has("nurse", "registry.read_admin_identifiers")
    assert not rbac.role_has("doctor", "registry.read_admin_identifiers")


def test_unknown_permission_fails_loudly():
    with pytest.raises(KeyError):
        rbac.role_has("doctor", "handoff.approvee")


def test_database_catalogue_matches_code(db):
    roles = {r.key: {p.key for p in r.permissions} for r in db.scalars(select(Role))}
    assert roles == {k: v["permissions"] for k, v in rbac.ROLES.items()}
