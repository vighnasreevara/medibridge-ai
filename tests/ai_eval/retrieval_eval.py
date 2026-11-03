"""Retrieval evaluation on the Synthea cardiology cohort (main database, after seeding + ingestion).

    uv run python -m tests.ai_eval.retrieval_eval

Cases are generated from the cohort: for every patient who has a report of a given kind, a plain-language
clinical question is asked (not the report's title) and the rank of the first report of that kind is recorded.
Each retrieval variant is scored on the same cases - BM25 only, dense only, hybrid (RRF) and hybrid + re-rank -
so the contribution of each part is measured, not assumed. A cross-patient leakage check fails the run if any
evidence belongs to another patient. Writes var/eval/retrieval.json.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from statistics import median

from sqlalchemy import select

from medibridge_api.config import REPO_ROOT, get_settings
from medibridge_api.db import get_sessionmaker
from medibridge_api.models import DocumentVersion, MedicalDocument
from medibridge_api.services.retrieval import VARIANTS, retrieve

# TUNED: the questions used while building the lay-term vocabulary in services/lexical.py (optimistic numbers).
# HELD_OUT: written afterwards with different wording and NOT used to tune anything; this is the honest estimate.
QUESTIONS = [  # (question in a clinician's words, title of the relevant report kind)
    ("how well is the heart pumping", "Echocardiogram report"),
    ("cholesterol control on statin therapy", "Lipid panel"),
    ("kidney function and potassium level", "Renal function and electrolytes"),
    ("anticoagulation INR monitoring", "Coagulation (INR)"),
    ("heart muscle injury marker after chest pain", "Cardiac biomarkers"),
    ("blood sugar control over three months", "HbA1c"),
    ("previous hospital admission", "Discharge summary"),
    ("stent or bypass operation", "Procedure report"),
    ("anaemia blood count", "Hemoglobin"),
]
HELD_OUT = [
    ("LV systolic function", "Echocardiogram report"),
    ("lipid profile on treatment", "Lipid panel"),
    ("electrolyte levels and renal clearance", "Renal function and electrolytes"),
    ("warfarin monitoring result", "Coagulation (INR)"),
    ("troponin level", "Cardiac biomarkers"),
    ("glycated haemoglobin", "HbA1c"),
    ("summary of the inpatient stay", "Discharge summary"),
    ("PCI or CABG details", "Procedure report"),
    ("red blood cell level", "Hemoglobin"),
]
PER_KIND = 12  # patients per question (spread across the cohort)
RERANK_CANDIDATE = "jinaai/jina-reranker-v1-tiny-en"  # measured even though re-ranking is off in the product


def cases(db, questions=QUESTIONS) -> list[tuple[uuid.UUID, str, str]]:
    rows = db.execute(select(MedicalDocument.patient_id, MedicalDocument.title)
                      .join(DocumentVersion, DocumentVersion.document_id == MedicalDocument.id)
                      .where(DocumentVersion.ingestion_status == "indexed")).all()
    by_kind: dict[str, list[uuid.UUID]] = defaultdict(list)
    for pid, title in rows:
        for _, kind in questions:
            if title.startswith(kind) and pid not in by_kind[kind]:
                by_kind[kind].append(pid)
    out = []
    for question, kind in questions:
        pids = sorted(by_kind[kind], key=str)
        step = max(1, len(pids) // PER_KIND)
        out += [(pid, question, kind) for pid in pids[::step][:PER_KIND]]
    return out


def metrics(ranks: list) -> dict:
    return {"hit_at_1": round(sum(r == 1 for r in ranks) / len(ranks), 3),
            "hit_at_3": round(sum(bool(r) and r <= 3 for r in ranks) / len(ranks), 3),
            "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 3)}


def score(db, bench: list[tuple[uuid.UUID, str, str]], variant: str) -> tuple[dict, int]:
    ranks, per_kind, ms, leaks = [], defaultdict(list), [], 0
    for pid, question, kind in bench:
        t = time.perf_counter()
        res = retrieve(db, patient_id=pid, query=question, limit=5, variant=variant)
        ms.append((time.perf_counter() - t) * 1000)
        leaks += res.dropped.get("other_patient", 0)
        rank = next((i + 1 for i, e in enumerate(res.evidence) if e.title.startswith(kind)), None)
        ranks.append(rank)
        per_kind[kind].append(rank)
    return {**metrics(ranks), "median_ms": int(median(ms)), "n": len(ranks),
            "by_question": {k: metrics(v) for k, v in per_kind.items()}}, leaks


def main() -> int:
    manifest = json.loads((REPO_ROOT / "var" / "seed-manifest.json").read_text(encoding="utf-8"))
    scen = {s["key"]: s for s in manifest["scenarios"]}
    # The "hybrid_rerank" variant uses the configured re-ranker, or the candidate when none is configured.
    get_settings().reranker_model = get_settings().reranker_model or RERANK_CANDIDATE
    report: dict = {"sets": {}, "reranker": get_settings().reranker_model}
    leaks = 0
    with get_sessionmaker()() as db:
        for set_name, questions in (("tuned", QUESTIONS), ("held_out", HELD_OUT)):
            bench = cases(db, questions)
            out = report["sets"][set_name] = {"questions": [q for q, _ in questions], "cases": len(bench), "variants": {}}
            print(f"[{set_name}] {len(bench)} cases over {len({c[0] for c in bench})} patients")
            for variant in VARIANTS:
                m, n_leaks = score(db, bench, variant)
                out["variants"][variant] = m
                leaks += n_leaks
                print(f"  {variant:14} hit@1 {m['hit_at_1']:.3f}  hit@3 {m['hit_at_3']:.3f}  MRR {m['mrr']:.3f}  median {m['median_ms']} ms")
        # Coverage behaviour: a corrupt upload is reported as failed, never as "nothing found".
        failed = retrieve(db, patient_id=uuid.UUID(scen["failed_document_ingestion"]["patient_id"]),
                          query="coronary angiogram", limit=5)
        report["failed_ingestion_case"] = {"evidence": len(failed.evidence), "coverage": failed.coverage}
    report["cross_patient_hits_dropped"] = leaks
    (REPO_ROOT / "var" / "eval").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "var" / "eval" / "retrieval.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"cross_patient_hits_dropped": leaks, "failed_ingestion_case": report["failed_ingestion_case"]["coverage"]},
                     indent=1, default=str))
    return 0 if leaks == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
