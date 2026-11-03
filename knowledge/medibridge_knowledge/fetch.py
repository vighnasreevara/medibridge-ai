"""Fetch and normalize the drug reference bundle.

    uv run python -m medibridge_knowledge.fetch [--offline]

Writes knowledge/sources/build/bundle.json (normalized) and knowledge/sources/manifest.json (sources, versions,
raw-file checksums). The bundle is the only input the Neo4j loader reads.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .sources import RAW_DIR, Fetcher

ROOT = Path(__file__).resolve().parents[1] / "sources"

# Scope: cardiology. Cardiovascular drug classes, cardiometabolic co-medications the cohort takes, and the
# non-cardiac drugs with well-known interactions with them (NSAIDs, macrolides, azoles, PDE5 inhibitors ...).
CARDIAC = {
    "anticoagulant": ("warfarin", "apixaban", "rivaroxaban", "dabigatran", "enoxaparin", "heparin"),
    "antiplatelet": ("aspirin", "clopidogrel", "prasugrel", "ticagrelor"),
    "beta_blocker": ("metoprolol", "carvedilol", "bisoprolol", "atenolol", "propranolol", "labetalol", "sotalol"),
    "ace_inhibitor": ("lisinopril", "enalapril", "ramipril"),
    "arb_arni": ("losartan", "valsartan", "sacubitril"),
    "calcium_channel_blocker": ("amlodipine", "diltiazem", "verapamil"),
    "diuretic_mra": ("furosemide", "torsemide", "hydrochlorothiazide", "chlorthalidone", "spironolactone",
                     "eplerenone"),
    "lipid_lowering": ("atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "ezetimibe"),
    "antiarrhythmic": ("amiodarone", "dronedarone", "flecainide", "dofetilide", "digoxin"),
    "antianginal": ("nitroglycerin", "isosorbide mononitrate", "ranolazine"),
    "heart_failure": ("dapagliflozin", "empagliflozin", "ivabradine", "hydralazine"),
}
COMEDICATION = ("metformin", "insulin isophane", "potassium chloride")
INTERACTING = ("ibuprofen", "naproxen", "celecoxib", "clarithromycin", "erythromycin", "azithromycin",
               "ketoconazole", "itraconazole", "fluconazole", "sildenafil", "tadalafil", "omeprazole",
               "acetaminophen")
INGREDIENTS = tuple(dict.fromkeys([*(d for group in CARDIAC.values() for d in group), *COMEDICATION, *INTERACTING]))


def build_bundle(fetcher: Fetcher) -> dict:
    drugs = []
    for name in INGREDIENTS:
        rxcui = fetcher.ingredient_rxcui(name)
        if not rxcui:
            print(f"  ! no RxNorm ingredient for '{name}' - skipped")
            continue
        brands = sorted({b["name"] for b in fetcher.related(rxcui, "BN")})
        medrt = fetcher.classes(rxcui, "medrt")
        epc = [c for c in fetcher.classes(rxcui, "fdaspl_epc") if c["rela"] == "has_epc" or c["class_type"] == "EPC"]
        label = fetcher.label(name)
        drugs.append({
            "name": name, "rxcui": rxcui, "synonyms": fetcher.synonyms(rxcui), "brands": brands,
            "epc": [{"id": c["class_id"], "name": c["class_name"]} for c in epc],
            "moa": [{"id": c["class_id"], "name": c["class_name"]} for c in medrt if c["rela"] == "has_moa"],
            "ci_with": sorted({c["class_name"] for c in medrt if c["rela"] == "ci_with"}),
            "may_treat": sorted({c["class_name"] for c in medrt if c["rela"] == "may_treat"}),
            "label": label,
        })
        print(f"  {name:20} rxcui={rxcui:>8} brands={len(brands):2} epc={len(epc)} ci_with={len(drugs[-1]['ci_with']):2} "
              f"label={'yes' if label else 'no'}")
    sources = [fetcher.source_record("rxnorm", fetcher.rxnorm_version()),
               fetcher.source_record("medrt", fetcher.rxclass_version()),
               fetcher.source_record("fdaspl_epc", fetcher.rxclass_version())]
    label_dates = sorted({d["label"]["meta_last_updated"] for d in drugs if d["label"] and d["label"]["meta_last_updated"]})
    sources.append(fetcher.source_record("openfda_label", label_dates[-1] if label_dates else "unknown"))
    return {"built_at": datetime.now(UTC).isoformat(), "sources": sources, "drugs": drugs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="use cached raw responses only")
    args = parser.parse_args()
    fetcher = Fetcher(offline=args.offline)
    bundle = build_bundle(fetcher)
    (ROOT / "build").mkdir(parents=True, exist_ok=True)
    (ROOT / "build" / "bundle.json").write_text(json.dumps(bundle, indent=1), encoding="utf-8")
    manifest = {"built_at": bundle["built_at"], "sources": bundle["sources"], "raw_dir": str(RAW_DIR.name),
                "raw_files": fetcher.manifest}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(bundle['drugs'])} drugs; sources: " + ", ".join(f"{s['id']}@{s['version']}" for s in bundle["sources"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
