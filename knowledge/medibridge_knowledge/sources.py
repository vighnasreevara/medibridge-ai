"""Fetch public, appropriately licensed drug reference data, caching every raw response with a checksum.

Sources (all US Government public data; no patient data is ever sent):
- RxNorm via RxNav REST (NLM)            - ingredient concepts, synonyms, brand names
- RxClass: MED-RT (VA/NLM)               - contraindicated-with-disease (ci_with), may_treat, mechanism of action
- RxClass: FDA SPL Established Pharmacologic Class (has_EPC)
- openFDA drug labels (FDA SPL, CC0)     - contraindications / drug interactions / boxed warning text for citation

The RxNav drug-interaction API was discontinued by NLM in January 2024 and is not used.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

RAW_DIR = Path(__file__).resolve().parents[1] / "sources" / "raw"
RXNAV = "https://rxnav.nlm.nih.gov/REST"
OPENFDA = "https://api.fda.gov/drug/label.json"

SOURCES = {
    "rxnorm": {"name": "RxNorm (via RxNav REST API)", "publisher": "U.S. National Library of Medicine",
               "license": "RxNorm SAB=RXNORM content: no licence restrictions (UMLS licence not required)",
               "url": "https://lhncbc.nlm.nih.gov/RxNav/"},
    "medrt": {"name": "MED-RT (via RxClass)", "publisher": "U.S. Department of Veterans Affairs / NLM",
              "license": "Public domain (U.S. Government work)", "url": "https://lhncbc.nlm.nih.gov/RxNav/applications/RxClassIntro.html"},
    "fdaspl_epc": {"name": "FDA Established Pharmacologic Class (via RxClass)", "publisher": "U.S. FDA",
                   "license": "Public domain (U.S. Government work)", "url": "https://www.fda.gov/industry/structured-product-labeling-resources/pharmacologic-class"},
    "openfda_label": {"name": "openFDA drug labeling (SPL)", "publisher": "U.S. FDA",
                      "license": "CC0 1.0 / public domain", "url": "https://open.fda.gov/apis/drug/label/"},
}


@dataclass
class Fetcher:
    offline: bool = False
    pause_s: float = 0.15  # RxNav asks clients to stay well under 20 requests/second
    manifest: dict = field(default_factory=dict)

    def __post_init__(self):
        self._client = httpx.Client(timeout=30, headers={"User-Agent": "MediBridge-AI-knowledge-builder/0.1 (research demo)"})

    def get_json(self, source: str, key: str, url: str) -> dict:
        path = RAW_DIR / source / f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
        if path.exists():
            data = path.read_bytes()
        elif self.offline:
            raise FileNotFoundError(f"{source}:{key} is not cached and offline mode is on")
        else:
            for attempt in range(4):
                resp = self._client.get(url)
                if resp.status_code == 404:
                    data = b"{}"
                    break
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.content
                break
            else:
                raise RuntimeError(f"{source}: giving up on {key}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            time.sleep(self.pause_s)
        self.manifest.setdefault(source, {})[key] = {"file": str(path.relative_to(RAW_DIR)).replace("\\", "/"),
                                                     "sha256": hashlib.sha256(data).hexdigest()}
        return json.loads(data or b"{}")

    # -------------------------------------------------------------- RxNav / RxClass
    def rxnorm_version(self) -> str:
        return self.get_json("rxnorm", "version", f"{RXNAV}/version.json").get("version", "unknown")

    def rxclass_version(self) -> str:
        # RxClass has no separate version endpoint; it is published as part of the RxNav release.
        return f"RxNav {self.rxnorm_version()}"

    def ingredient_rxcui(self, name: str) -> str | None:
        data = self.get_json("rxnorm", f"rxcui:{name}", f"{RXNAV}/rxcui.json?name={quote(name)}&search=2")
        ids = (data.get("idGroup") or {}).get("rxnormId") or []
        return ids[0] if ids else None

    def related(self, rxcui: str, tty: str) -> list[dict]:
        data = self.get_json("rxnorm", f"related:{rxcui}:{tty}", f"{RXNAV}/rxcui/{rxcui}/related.json?tty={tty}")
        out = []
        for group in (data.get("relatedGroup") or {}).get("conceptGroup") or []:
            out.extend(group.get("conceptProperties") or [])
        return out

    def synonyms(self, rxcui: str) -> list[str]:
        data = self.get_json("rxnorm", f"names:{rxcui}", f"{RXNAV}/rxcui/{rxcui}/allProperties.json?prop=names")
        props = (data.get("propConceptGroup") or {}).get("propConcept") or []
        return sorted({p["propValue"] for p in props if p.get("propName") in ("RxNorm Name", "RxNorm Synonym")})

    def classes(self, rxcui: str, source: str) -> list[dict]:
        rela_source = {"medrt": "MEDRT", "fdaspl_epc": "FDASPL"}[source]
        data = self.get_json(source, f"classes:{rxcui}",
                             f"{RXNAV}/rxclass/class/byRxcui.json?rxcui={rxcui}&relaSource={rela_source}")
        seen, out = set(), []
        for item in (data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []:
            c = item["rxclassMinConceptItem"]
            key = (item.get("rela"), c["classId"])
            if key not in seen:
                seen.add(key)
                out.append({"rela": item.get("rela"), "class_id": c["classId"], "class_name": c["className"],
                            "class_type": c["classType"]})
        return out

    # -------------------------------------------------------------- openFDA
    def label(self, generic_name: str) -> dict | None:
        q = quote(f'openfda.generic_name:"{generic_name}"')
        data = self.get_json("openfda_label", f"label:{generic_name}", f"{OPENFDA}?search={q}&limit=5")
        results = data.get("results") or []
        if not results:
            return None
        # Prefer a single-ingredient label whose generic name matches exactly, most recently effective.
        def score(r):
            names = [n.lower() for n in (r.get("openfda") or {}).get("generic_name", [])]
            exact = any(n == generic_name.lower() for n in names)
            return (exact, r.get("effective_time", ""))
        best = max(results, key=score)
        return {
            "set_id": best.get("set_id"), "version": best.get("version"), "effective_time": best.get("effective_time"),
            "brand_name": ((best.get("openfda") or {}).get("brand_name") or [None])[0],
            "generic_name": ((best.get("openfda") or {}).get("generic_name") or [generic_name])[0],
            "sections": {s: " ".join(best[s]) for s in ("boxed_warning", "contraindications", "drug_interactions",
                                                       "warnings_and_cautions", "warnings") if best.get(s)},
            "meta_last_updated": (data.get("meta") or {}).get("last_updated"),
        }

    def source_record(self, key: str, version: str) -> dict:
        return {"id": key, **SOURCES[key], "version": version, "retrieved_at": datetime.now(UTC).isoformat()}
