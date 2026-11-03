"""Sync the RBAC catalogue (code is the source of truth) into auth.roles / auth.permissions.

Runs as the database owner: the application role cannot modify roles or permissions.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from medibridge_api.models import Permission, Role, RolePermission
from medibridge_api.security import rbac


def sync_rbac(owner_engine: Engine) -> dict:
    with Session(owner_engine) as db:
        perms: dict[str, Permission] = {p.key: p for p in db.scalars(select(Permission))}
        for p in rbac.PERMISSIONS:
            row = perms.get(p.key) or Permission(key=p.key)
            row.description, row.patient_scoped = p.description, p.patient_scoped
            db.add(row)
            perms[p.key] = row
        roles: dict[str, Role] = {r.key: r for r in db.scalars(select(Role))}
        for key, spec in rbac.ROLES.items():
            row = roles.get(key) or Role(key=key)
            row.label, row.description = spec["label"], spec["description"]
            db.add(row)
            roles[key] = row
        db.flush()
        db.execute(delete(RolePermission))
        for key, spec in rbac.ROLES.items():
            for perm in spec["permissions"]:
                db.add(RolePermission(role_id=roles[key].id, permission_id=perms[perm].id))
        db.commit()
        return {"roles": len(roles), "permissions": len(perms)}
