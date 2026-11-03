"""Operator commands.

    uv run python -m medibridge_api.cli ingest [--retry-failed]   # index pending document versions now
    uv run python -m medibridge_api.cli reindex                   # rebuild the vector index (e.g. new embedding model)
    uv run python -m medibridge_api.cli verify-audit              # check the audit hash chain
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import update

from .db import get_sessionmaker
from .models import DocumentVersion
from .services import audit, ingestion, vector_index


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("--retry-failed", action="store_true")
    sub.add_parser("reindex")
    sub.add_parser("verify-audit")
    args = parser.parse_args()

    if args.cmd in ("ingest", "reindex"):
        with get_sessionmaker()() as db:
            if args.cmd == "reindex":
                db.execute(update(DocumentVersion).where(DocumentVersion.ingestion_status == "indexed")
                           .values(ingestion_status="pending"))
                db.commit()
            ids = ingestion.pending_versions(db, include_failed=getattr(args, "retry_failed", False) or args.cmd == "reindex")
        summary: dict[str, int] = {}
        for i, vid in enumerate(ids, 1):
            result = ingestion.ingest_version(vid)
            key = result["status"] if result["status"] != "failed" else f"failed:{result.get('error')}"
            summary[key] = summary.get(key, 0) + 1
            print(f"[{i}/{len(ids)}] {vid} {result}")
        print(json.dumps({"summary": summary, "index": vector_index.stats()}, indent=1))
        return 0
    if args.cmd == "verify-audit":
        with get_sessionmaker()() as db:
            print(json.dumps(audit.verify_chain(db), indent=1, default=str))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
