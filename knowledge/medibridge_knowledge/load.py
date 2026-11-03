"""Load a versioned knowledge release into Neo4j.

    uv run python -m medibridge_knowledge.load

1. Verifies every curated statement: its quote must occur verbatim (whitespace-normalised) in the cited FDA
   label section from the fetched bundle. Any failure aborts the build - no unsupported edge is ever loaded.
2. Writes all nodes with a ``release`` id in batches (UNWIND, ``BATCH`` rows per transaction), flips the active
   KnowledgeRelease, then removes older releases.
No LLM is involved at any step.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from neo4j import GraphDatabase

from medibridge_api.config import get_settings

ROOT = Path(__file__).resolve().parents[1] / "sources"
CURATED = ROOT / "curated" / "cardiac_curated.json"
BATCH = 200
DAILYMED = "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid="


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verify(bundle: dict, curated: dict) -> tuple[list[dict], list[str]]:
    """Return (evidence records used, problems). Evidence id = set_id:version:section."""
    drugs = {d["name"]: d for d in bundle["drugs"]}
    problems: list[str] = []
    evidence: dict[str, dict] = {}

    def check(ev: dict, where: str) -> str | None:
        drug = drugs.get(ev["label_of"])
        label = drug and drug["label"]
        if not label:
            problems.append(f"{where}: no label for {ev['label_of']}")
            return None
        text = label["sections"].get(ev["section"], "")
        if _norm(ev["quote"]) not in _norm(text):
            problems.append(f"{where}: quote not found in {ev['label_of']} label section '{ev['section']}'")
            return None
        eid = f"{label['set_id']}:{label['version']}:{ev['section']}"
        evidence[eid] = {"id": eid, "set_id": label["set_id"], "version": str(label["version"]),
                         "effective_time": label["effective_time"], "section": ev["section"], "drug": ev["label_of"],
                         "title": f"{label.get('brand_name') or ev['label_of']} label - {ev['section'].replace('_', ' ')}",
                         "url": DAILYMED + label["set_id"], "text": text}
        return eid

    for item in curated["interactions"]:
        for name in item["drugs"]:
            if name not in drugs:
                problems.append(f"{item['id']}: unknown drug {name}")
        item["_evidence"] = [(check(ev, item["id"]), ev["quote"]) for ev in item["evidence"]]
        basis = item.get("class_basis")
        if basis and basis["epc"] not in {c["name"] for c in drugs.get(basis["drug"], {}).get("epc", [])}:
            problems.append(f"{item['id']}: {basis['drug']} is not in EPC class {basis['epc']}")
    for cc in curated.get("condition_cautions", []):
        for name in cc["drugs"]:
            if name not in drugs:
                problems.append(f"{cc['id']}: unknown drug {name}")
        cc["_evidence"] = [(check(e, cc["id"]), e["quote"]) for e in cc["evidence"]]
    for item in curated["interactions"]:
        limit = item.get("dose_limit")
        if limit and limit["drug"] not in item["drugs"]:
            problems.append(f"{item['id']}: dose limit names {limit['drug']}, which is not part of the interaction")
    for a in curated["allergy_substances"]:
        a["_evidence"] = [(check(a["evidence"], a["id"]), a["evidence"]["quote"])] if a.get("evidence") else []
        for c in a.get("cautions", []):
            c["_evidence"] = [(check(c["evidence"], a["id"]), c["evidence"]["quote"])] if c.get("evidence") else []
        for m in a["maps_to"] + a.get("cautions", []):
            if m["kind"] == "epc" and not any(m["name"] in {c["name"] for c in d["epc"]} for d in drugs.values()):
                problems.append(f"{a['id']}: no drug in scope belongs to EPC '{m['name']}'")
            if m["kind"] == "ingredient" and m["name"] not in drugs:
                problems.append(f"{a['id']}: unknown ingredient {m['name']}")
    return list(evidence.values()), problems


def _group(name: str) -> str:
    from .fetch import CARDIAC, COMEDICATION

    return next((g for g, members in CARDIAC.items() if name in members),
                "cardiometabolic_comedication" if name in COMEDICATION else "interacting_non_cardiac")


def _batched(db, query: str, rows: list[dict], label: str, **params) -> None:
    for start in range(0, len(rows), BATCH):
        db.run(query, rows=rows[start:start + BATCH], **params)
    print(f"  loaded {len(rows):5d} {label} in {max(1, -(-len(rows) // BATCH))} batch(es)")


def load(bundle: dict, curated: dict, evidence: list[dict]) -> dict:
    s = get_settings()
    digest = hashlib.sha256((json.dumps(bundle, sort_keys=True) + json.dumps(curated, sort_keys=True,
                                                                                  default=str)).encode()).hexdigest()[:10]
    rxnorm = next(x["version"] for x in bundle["sources"] if x["id"] == "rxnorm")
    release = f"kr-{datetime.strptime(rxnorm, '%d-%b-%Y').strftime('%Y%m%d')}-{digest}"
    drugs = bundle["drugs"]
    with GraphDatabase.driver(s.neo4j_uri, auth=("neo4j", s.neo4j_password.get_secret_value())) as driver:
        with driver.session() as db:
            for stmt in (
                "CREATE CONSTRAINT kr_id IF NOT EXISTS FOR (r:KnowledgeRelease) REQUIRE r.id IS UNIQUE",
                "CREATE INDEX drug_release IF NOT EXISTS FOR (d:ActiveIngredient) ON (d.release, d.rxcui)",
                "CREATE INDEX drug_name IF NOT EXISTS FOR (d:ActiveIngredient) ON (d.release, d.name)",
                "CREATE INDEX class_release IF NOT EXISTS FOR (c:DrugClass) ON (c.release, c.name)",
                "CREATE INDEX cond_release IF NOT EXISTS FOR (c:MedicalCondition) ON (c.release, c.name)",
                "CREATE INDEX evidence_release IF NOT EXISTS FOR (e:EvidenceSource) ON (e.release, e.id)",
            ):
                db.run(stmt)
            db.run("MATCH (n {release: $r}) DETACH DELETE n", r=release)  # idempotent rebuild of the same release
            db.run("""UNWIND $sources AS s CREATE (:KnowledgeSource {release: $r, source_id: s.id, name: s.name,
                      publisher: s.publisher, version: s.version, license: s.license, url: s.url,
                      retrieved_at: s.retrieved_at})""", sources=bundle["sources"], r=release)
            _batched(db, """UNWIND $rows AS d
                      CREATE (i:Drug:ActiveIngredient {release: $r, rxcui: d.rxcui, name: d.name,
                              synonyms: d.synonyms, brands: d.brands, search_terms: d.terms, scope_group: d.group})
                      WITH i, d UNWIND d.brands AS b
                      CREATE (p:Drug:Product {release: $r, name: b, kind: 'brand'})-[:CONTAINS_INGREDIENT
                             {source: 'rxnorm'}]->(i)""",
                     [{**d, "terms": sorted({_norm(t) for t in [d["name"], *d["synonyms"], *d["brands"]]}),
                       "group": _group(d["name"])} for d in drugs], "ingredients (+ brand products)", r=release)
            classes = [{"rxcui": d["rxcui"], "id": c["id"], "name": c["name"], "type": t, "source": src}
                       for d in drugs for t, src, key in (("EPC", "fdaspl_epc", "epc"), ("MOA", "medrt", "moa"))
                       for c in d[key]]
            _batched(db, """UNWIND $rows AS row
                      MERGE (c:DrugClass {release: $r, name: row.name, type: row.type})
                        ON CREATE SET c.class_id = row.id, c.source = row.source
                      WITH c, row MATCH (i:ActiveIngredient {release: $r, rxcui: row.rxcui})
                      MERGE (i)-[:BELONGS_TO_CLASS {source: row.source}]->(c)""", classes, "class memberships",
                     r=release)
            medrt_version = next(x["version"] for x in bundle["sources"] if x["id"] == "medrt")
            rows = [{"rxcui": d["rxcui"], "cond": c, "rel": rel} for d in drugs
                    for rel, key in (("CONTRAINDICATED_FOR", "ci_with"), ("MAY_TREAT", "may_treat")) for c in d[key]]
            for rel in ("CONTRAINDICATED_FOR", "MAY_TREAT"):
                _batched(db, f"""UNWIND $rows AS row
                           MERGE (c:MedicalCondition {{release: $r, name: row.cond}})
                           WITH c, row MATCH (i:ActiveIngredient {{release: $r, rxcui: row.rxcui}})
                           MERGE (i)-[:{rel} {{source: 'medrt', source_version: $v}}]->(c)""",
                         [x for x in rows if x["rel"] == rel], f"MED-RT {rel.lower()} links", r=release,
                         v=medrt_version)
            _batched(db, """UNWIND $rows AS e CREATE (:EvidenceSource {release: $r, id: e.id, kind: 'fda_label_section',
                      set_id: e.set_id, version: e.version, effective_time: e.effective_time, section: e.section,
                      drug: e.drug, title: e.title, url: e.url, text: e.text})""", evidence, "label evidence sections",
                     r=release)
            _batched(db, """UNWIND $rows AS item
                      CREATE (x:Interaction {release: $r, id: item.id, severity: item.severity, summary: item.summary,
                              dose_limit: item.dose_limit, status: 'demo_curated_not_clinically_reviewed',
                              curated_by: $by})
                      WITH x, item UNWIND item.drugs AS name
                      MATCH (i:ActiveIngredient {release: $r, name: name}) MERGE (i)-[:INTERACTS_WITH]->(x)
                      WITH DISTINCT x, item UNWIND item.ev AS ev
                      MATCH (e:EvidenceSource {release: $r, id: ev[0]}) MERGE (x)-[:SUPPORTED_BY {quote: ev[1]}]->(e)""",
                     [{"id": i["id"], "severity": i["severity"], "summary": i["summary"], "drugs": i["drugs"],
                       "dose_limit": json.dumps(i["dose_limit"]) if i.get("dose_limit") else None,
                       "ev": [list(e) for e in i["_evidence"]]} for i in curated["interactions"]],
                     "curated interactions", r=release, by=curated["curated_by"])
            _batched(db, """UNWIND $rows AS cc
                      CREATE (x:ConditionCaution {release: $r, id: cc.id, severity: cc.severity, summary: cc.summary,
                              condition_terms: cc.terms, status: 'demo_curated_not_clinically_reviewed'})
                      WITH x, cc UNWIND cc.drugs AS name
                      MATCH (i:ActiveIngredient {release: $r, name: name}) MERGE (i)-[:CAUTION_IN_CONDITION]->(x)
                      WITH DISTINCT x, cc UNWIND cc.ev AS ev
                      MATCH (e:EvidenceSource {release: $r, id: ev[0]}) MERGE (x)-[:SUPPORTED_BY {quote: ev[1]}]->(e)""",
                     [{"id": c["id"], "severity": c["severity"], "summary": c["summary"], "drugs": c["drugs"],
                       "terms": [_norm(t) for t in c["condition_terms"]], "ev": [list(e) for e in c["_evidence"]]}
                      for c in curated.get("condition_cautions", [])], "drug-condition cautions", r=release)
            for a in curated["allergy_substances"]:
                db.run("CREATE (:AllergySubstance {release: $r, id: $id, terms: $terms})", r=release, id=a["id"],
                       terms=[_norm(t) for t in a["terms"]])
                for rel, items in (("CONTRAINDICATED_FOR", a["maps_to"]), ("CAUTION_WITH", a.get("cautions", []))):
                    for m in items:
                        target = ("MATCH (t:DrugClass {release: $r, name: $name, type: 'EPC'})" if m["kind"] == "epc"
                                  else "MATCH (t:ActiveIngredient {release: $r, name: $name})")
                        db.run(f"""MATCH (a:AllergySubstance {{release: $r, id: $id}}) {target}
                                   MERGE (a)-[:{rel} {{basis: $basis}}]->(t)""",
                               r=release, id=a["id"], name=m["name"], basis=m["basis"])
                        for eid, quote in m.get("_evidence", []):
                            db.run(f"""MATCH (a:AllergySubstance {{release: $r, id: $id}})-[x:{rel}]->(t {{name: $name}})
                                       MATCH (e:EvidenceSource {{release: $r, id: $eid}})
                                       MERGE (a)-[:SUPPORTED_BY {{quote: $q, for_target: $name}}]->(e)""",
                                   r=release, id=a["id"], name=m["name"], eid=eid, q=quote)
                for eid, quote in a["_evidence"]:
                    db.run("""MATCH (a:AllergySubstance {release: $r, id: $id}) MATCH (e:EvidenceSource {release: $r, id: $eid})
                              MERGE (a)-[:SUPPORTED_BY {quote: $q}]->(e)""", r=release, id=a["id"], eid=eid, q=quote)
            counts = {row["label"]: row["n"] for row in db.run(
                "MATCH (n {release: $r}) UNWIND labels(n) AS label RETURN label, count(*) AS n", r=release)}
            db.run("""MERGE (k:KnowledgeRelease {id: $r})
                      SET k.built_at = $at, k.bundle_built_at = $bat, k.counts = $counts, k.sources = $sources,
                          k.curated_status = 'demo_curated_not_clinically_reviewed', k.scope = $scope,
                          k.ingredients = $ingredients,
                          k.condition_aliases = $aliases""",
                   r=release, at=datetime.now(UTC).isoformat(), bat=bundle["built_at"], counts=json.dumps(counts),
                   sources=json.dumps(bundle["sources"]),
                   aliases=json.dumps({k: v for k, v in curated["condition_aliases"].items() if not k.startswith("_")}),
                   scope=curated.get("scope", "general"), ingredients=len(drugs))
            # Flip the active release only after everything above succeeded, then drop older releases.
            db.run("MATCH (k:KnowledgeRelease) SET k.active = (k.id = $r)", r=release)
            old = [row["id"] for row in db.run("MATCH (k:KnowledgeRelease) WHERE k.id <> $r RETURN k.id AS id", r=release)]
            for rid in old:
                db.run("MATCH (n {release: $old}) DETACH DELETE n", old=rid)
                db.run("MATCH (k:KnowledgeRelease {id: $old}) DELETE k", old=rid)
    return {"release": release, "counts": counts, "removed_releases": old}


def main() -> int:
    bundle = json.loads((ROOT / "build" / "bundle.json").read_text(encoding="utf-8"))
    curated = json.loads(CURATED.read_text(encoding="utf-8"))
    evidence, problems = verify(bundle, curated)
    if problems:
        print("Curated knowledge failed verification; nothing was loaded:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    result = load(bundle, curated, evidence)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
