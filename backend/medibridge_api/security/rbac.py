"""Role-based access control catalogue (deny by default).

This module is the source of truth; the seed syncs it into ``auth.roles`` / ``auth.permissions``.
Permissions marked ``patient_scoped`` additionally require an active care-team assignment for the
patient (see ``scope.py``). Administrators manage the system but have no clinical-record access.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    key: str
    description: str
    patient_scoped: bool = False


PERMISSIONS: tuple[PermissionDef, ...] = (
    # Registry (minimal demographics: name, DOB, MRN) - needed to find an arriving patient.
    PermissionDef("registry.search", "Search the patient registry (minimal demographics)"),
    PermissionDef("registry.register", "Register a new patient in the master patient index"),
    PermissionDef("registry.read_admin_identifiers", "View administrative identifiers and contact details"),
    # Clinical record (patient-scoped)
    PermissionDef("patient.read_clinical", "Read a patient's clinical record", True),
    PermissionDef("encounter.create", "Create an encounter for a verified patient"),
    PermissionDef("encounter.read", "Read encounter details", True),
    PermissionDef("consent.record", "Record patient consent", True),
    PermissionDef("intake.record", "Record and upload intake audio", True),
    PermissionDef("transcript.review", "Review and correct transcripts", True),
    PermissionDef("workflow.start", "Start or resume the intake workflow", True),
    PermissionDef("vitals.record", "Record vital signs", True),
    PermissionDef("document.read", "Read authorized medical documents", True),
    PermissionDef("document.upload", "Upload medical documents", True),
    PermissionDef("medication.read", "Read medication and allergy records", True),
    PermissionDef("handoff.read", "Read clinical handoffs", True),
    PermissionDef("handoff.edit", "Edit handoff drafts", True),
    PermissionDef("handoff.approve", "Approve or reject handoffs", True),
    PermissionDef("note.write", "Draft clinical documentation", True),
    PermissionDef("note.approve", "Approve and finalize clinical documentation", True),
    PermissionDef("prescription.create", "Create and approve prescriptions", True),
    # Operations (no clinical content)
    PermissionDef("agents.monitor", "View agent execution status (no clinical content)"),
    PermissionDef("observability.read", "View latency, failures and model metrics"),
    PermissionDef("audit.read", "Read audit and governance records"),
    PermissionDef("admin.users", "Manage users and roles"),
    PermissionDef("admin.care_team", "Manage care-team assignments"),
    PermissionDef("admin.config", "Manage providers, models, feature flags and guardrail settings"),
    PermissionDef("admin.diagnostics", "Run system diagnostics"),
)

ROLES: dict[str, dict] = {
    "doctor": {
        "label": "Doctor",
        "description": "Reviews evidence, edits and approves handoffs and documentation, prescribes.",
        "permissions": {
            "registry.search", "registry.register", "patient.read_clinical", "encounter.create", "encounter.read",
            "consent.record",
            "intake.record", "transcript.review", "workflow.start", "vitals.record", "document.read",
            "document.upload", "medication.read", "handoff.read", "handoff.edit", "handoff.approve", "note.write",
            "note.approve", "prescription.create", "agents.monitor", "observability.read",
        },
    },
    "nurse": {
        "label": "Nurse / Intake Operator",
        "description": "Registers encounters, records consent and intake audio, reviews transcripts.",
        "permissions": {
            "registry.search", "registry.register", "patient.read_clinical", "encounter.create", "encounter.read",
            "consent.record",
            "intake.record", "transcript.review", "workflow.start", "vitals.record", "document.read",
            "document.upload", "medication.read", "handoff.read", "agents.monitor",
        },
    },
    "admin": {
        "label": "Administrator",
        "description": "Manages users, configuration and governance. No clinical-record access.",
        "permissions": {
            "registry.search", "registry.read_admin_identifiers", "agents.monitor", "observability.read",
            "audit.read", "admin.users", "admin.care_team", "admin.config", "admin.diagnostics",
        },
    },
}

_BY_KEY = {p.key: p for p in PERMISSIONS}


def is_patient_scoped(permission: str) -> bool:
    return _BY_KEY[permission].patient_scoped


def role_has(role_key: str, permission: str) -> bool:
    if permission not in _BY_KEY:
        raise KeyError(f"Unknown permission {permission!r}")  # typos must fail loudly, never allow
    return permission in ROLES.get(role_key, {}).get("permissions", set())


def _validate() -> None:
    for role, spec in ROLES.items():
        unknown = spec["permissions"] - _BY_KEY.keys()
        if unknown:
            raise RuntimeError(f"Role {role} references unknown permissions: {sorted(unknown)}")


_validate()
