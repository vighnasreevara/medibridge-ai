"""Seed the local database: cardiology staff, the Synthea cardiac cohort and the named demo scenarios.

    uv run python -m medibridge_seed.synthea generate   # once: generate the Synthea cohort (Java 17+)
    uv run python -m medibridge_seed                    # first run only (refuses if patients exist)
    uv run python -m medibridge_seed --reset            # DESTROYS the local database schemas and reseeds

Demo account passwords are generated randomly on every seed and written to var/demo-credentials.txt
(git-ignored). Nothing is hard-coded in the frontend or the repository.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select

from medibridge_api.config import REPO_ROOT, get_settings
from medibridge_api.db import get_sessionmaker
from medibridge_api.models import DoctorProfile, Patient, Role, User
from medibridge_api.security import passwords
from medibridge_api.services import audit

from . import cohort
from .catalog import STAFF
from .reference import sync_rbac


def _password() -> str:
    words = ("amber", "birch", "cobalt", "delta", "ember", "fjord", "glade", "harbor", "indigo", "juniper", "kelp",
             "lumen", "meadow", "nectar", "orbit", "prism", "quartz", "ridge", "summit", "tundra")
    return "-".join(secrets.choice(words) for _ in range(4)) + f"-{secrets.randbelow(90) + 10}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all schemas first (destructive)")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    settings = get_settings()
    owner = create_engine(settings.database_url("owner"))
    cfg = Config(str(REPO_ROOT / "database" / "alembic.ini"))
    if args.reset:
        print(f"Resetting database '{settings.postgres_db}' ...")
        command.downgrade(cfg, "base")
        # Vectors point at document rows that no longer exist: drop them too (no orphaned patient data).
        from medibridge_api.services import vector_index

        try:
            vc = vector_index.client()
            for col in vc.get_collections().collections:
                if col.name.startswith(vector_index.ALIAS + "__"):
                    vc.delete_collection(col.name)
                    print(f"Dropped vector collection {col.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: could not clear the vector index ({type(exc).__name__}); run cli reindex later")
    command.upgrade(cfg, "head")
    print("RBAC catalogue:", sync_rbac(owner))

    with get_sessionmaker()() as db:
        if db.scalar(select(func.count()).select_from(Patient)):
            print("Patients already exist. Use --reset to rebuild the synthetic dataset.", file=sys.stderr)
            return 1
        roles = {r.key: r for r in db.scalars(select(Role))}
        credentials, doctors, nurses = [], [], []
        for role, given, family, specialty in STAFF:
            pw = _password()
            prefix = "dr." if role == "doctor" else ""
            user = User(email=f"{prefix}{given}.{family}@medibridge.test".lower(),
                        display_name=(f"Dr. {given} {family}" if role == "doctor" else f"{given} {family}"),
                        password_hash=passwords.hash_password(pw), role_id=roles[role].id)
            db.add(user)
            db.flush()
            if role == "doctor":
                db.add(DoctorProfile(user_id=user.id, specialty=specialty, department=specialty,
                                     license_number=f"SYN-LIC-{secrets.randbelow(900000) + 100000}"))
                doctors.append(user)
            elif role == "nurse":
                nurses.append(user)
            credentials.append((roles[role].label, user.display_name, user.email, pw))

        try:
            result = cohort.build(db, doctors, nurses, args.today)
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        audit.record(db, action="system.seed", category="administration", outcome="success",
                     details={"counts": result["counts"], "scenarios": len(result["scenarios"])})
        db.commit()

    var = Path(REPO_ROOT) / "var"
    var.mkdir(exist_ok=True)
    (var / "seed-manifest.json").write_text(json.dumps({"seeded_for": args.today.isoformat(), **result}, indent=2),
                                            encoding="utf-8")
    lines = ["MediBridge AI demo accounts (SYNTHETIC; regenerated on every seed)", ""]
    lines += [f"{label:<26} {name:<28} {email:<40} {pw}" for label, name, email, pw in credentials]
    (var / "demo-credentials.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("Source:", json.dumps(result["source"]))
    print("Seeded:", json.dumps(result["counts"]))
    print(f"Scenarios: {len(result['scenarios'])} (var/seed-manifest.json)")
    print("Demo accounts written to var/demo-credentials.txt:")
    print("\n".join(lines[2:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
