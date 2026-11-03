"""Deterministic queries over the active knowledge release (guardrails 5 and 6).

The medication-safety screen:
- resolves medication text to ingredients by exact name / synonym / brand; near-misses (ASR errors such as
  "Chlorithromycin", partial names such as "atorva-something") become *suggestions* marked ``inferred`` and
  are screened only as "possible" alerts that depend on confirming the medication;
- reports interactions, allergy matches, curated drug-condition cautions and MED-RT condition relationships, each
  with cited evidence. Label dose limits (e.g. "do not exceed simvastatin 20 mg daily" with amlodipine) are checked
  against the recorded dose and frequency;
- MED-RT condition links are coarse (heart failure is listed for the beta-blockers that treat it), so they are only
  ever reported as "caution - check the label"; only label-quoted curated edges can be blocking;
- reports how complete the screen is. It never states that a combination is safe.
No LLM participates; nothing here prescribes.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from itertools import combinations

from neo4j import Driver

FUZZY_THRESHOLD = 0.82
SEVERITY_RANK = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3, "caution": 4}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


FREQUENCY_PER_DAY = {"once daily": 1, "daily": 1, "once a day": 1, "every day": 1, "at bedtime": 1, "nightly": 1,
                     "twice daily": 2, "twice a day": 2, "every 12 hours": 2, "three times daily": 3,
                     "three times a day": 3, "every 8 hours": 3, "four times daily": 4, "every 6 hours": 4}


def daily_mg(fact: dict) -> float | None:
    """Recorded daily dose in mg from ``dose_text`` and ``frequency_text``; None when either is missing or unclear."""
    dose = re.search(r"(\d+(?:\.\d+)?)\s*mg\b", (fact.get("dose_text") or "").lower())
    freq = (fact.get("frequency_text") or "").lower().strip()
    per_day = next((n for k, n in FREQUENCY_PER_DAY.items() if k in freq), None)
    if not dose or per_day is None or "as needed" in freq:
        return None
    return float(dose.group(1)) * per_day


def components(text: str) -> list[str]:
    """Split a combination product ("hydrochlorothiazide 12.5 MG / lisinopril 10 MG") into its parts."""
    parts = [p.strip() for p in re.split(r"\s+/\s+|\s+and\s+(?=[a-z])", text) if p.strip()]
    return parts if len(parts) > 1 else [text]


@dataclass
class Resolution:
    text: str
    match_type: str  # exact | synonym | brand | suggestion | unresolved
    rxcui: str | None = None
    name: str | None = None
    candidates: list[dict] = field(default_factory=list)
    certainty: str = "stated"  # stated | inferred

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _fact(m: dict) -> dict:
    out = {"text": m.get("component_of") or m["text"], "ref": m.get("ref"), "source_kind": m.get("source_kind")}
    for k in ("dose_text", "frequency_text"):
        if m.get(k):
            out[k] = m[k]
    return out


class KnowledgeGraph:
    def __init__(self, driver: Driver):
        self.driver = driver
        self._index: dict[str, tuple[str, str, str]] | None = None  # term -> (rxcui, name, match_type)
        self.release: dict | None = None

    # ---------------------------------------------------------------- release + term index
    def active_release(self) -> dict | None:
        with self.driver.session() as db:
            rec = db.run("MATCH (k:KnowledgeRelease {active: true}) RETURN k").single()
        if not rec:
            return None
        k = dict(rec["k"])
        k["sources"] = json.loads(k.get("sources") or "[]")
        k["counts"] = json.loads(k.get("counts") or "{}")
        k["condition_aliases"] = json.loads(k.get("condition_aliases") or "{}")
        return k

    def _ensure_index(self) -> None:
        # The active release can be switched while this process is running (a new build removes the old one).
        # Re-check on every use: querying a removed release would silently return "no alerts".
        if self._index is not None:
            with self.driver.session() as db:
                rec = db.run("MATCH (k:KnowledgeRelease {active: true}) RETURN k.id AS id").single()
            if rec and self.release and rec["id"] == self.release["id"]:
                return
            self._index = None
        self.release = self.active_release()
        if not self.release:
            raise LookupError("No active knowledge release is loaded in Neo4j")
        index: dict[str, tuple[str, str, str]] = {}
        with self.driver.session() as db:
            for row in db.run("""MATCH (i:ActiveIngredient {release: $r})
                                 RETURN i.rxcui AS rxcui, i.name AS name, i.synonyms AS syn, i.brands AS brands""",
                              r=self.release["id"]):
                index[norm(row["name"])] = (row["rxcui"], row["name"], "exact")
                for s in row["syn"] or []:
                    index.setdefault(norm(s), (row["rxcui"], row["name"], "synonym"))
                for b in row["brands"] or []:
                    index.setdefault(norm(b), (row["rxcui"], row["name"], "brand"))
        self._index = index

    def vocabulary(self) -> dict[str, str]:
        """normalized term -> ingredient name (for transcript drug-name checks)."""
        self._ensure_index()
        return {t: v[1] for t, v in self._index.items()}

    def resolve(self, text: str) -> Resolution:
        self._ensure_index()
        assert self._index is not None
        t = norm(text)
        if t in self._index:
            rxcui, name, kind = self._index[t]
            return Resolution(text, kind, rxcui, name)
        # Longest known term contained in the text, e.g. "lisinopril 10 mg tablet", "my warfarin".
        tokens = t.split()
        for size in range(min(4, len(tokens)), 0, -1):
            for i in range(len(tokens) - size + 1):
                gram = " ".join(tokens[i:i + size])
                if gram in self._index and len(gram) >= 4:
                    rxcui, name, kind = self._index[gram]
                    return Resolution(text, kind, rxcui, name)
        # Near misses: suggestions only.
        candidates: dict[str, dict] = {}
        # Also try adjacent tokens joined: ASR often splits a name ("a torva something" -> "atorva").
        joined = [a + b for a, b in zip(tokens, tokens[1:])]
        for tok in [t, *tokens, *joined]:
            if len(tok) < 5:
                continue
            for term, (rxcui, name, _) in self._index.items():
                ratio = difflib.SequenceMatcher(None, tok, term).ratio()
                prefix = len(tok) >= 5 and term.startswith(tok.split()[0][:6]) and len(tok.split()[0]) >= 5
                if ratio >= FUZZY_THRESHOLD or prefix:
                    score = max(ratio, 0.75 if prefix else 0)
                    if rxcui not in candidates or candidates[rxcui]["score"] < score:
                        candidates[rxcui] = {"rxcui": rxcui, "name": name, "score": round(score, 2),
                                             "basis": "spelling_similarity" if ratio >= FUZZY_THRESHOLD else "name_prefix"}
        ranked = sorted(candidates.values(), key=lambda c: -c["score"])[:3]
        if ranked:
            return Resolution(text, "suggestion", ranked[0]["rxcui"], ranked[0]["name"], ranked, certainty="inferred")
        return Resolution(text, "unresolved")

    # ---------------------------------------------------------------- lookups
    def lookup_drug(self, text: str) -> dict:
        res = self.resolve(text)
        out: dict = {"resolution": res.as_dict(), "release": self.release["id"] if self.release else None}
        if not res.rxcui:
            return out
        with self.driver.session() as db:
            rec = db.run("""MATCH (i:ActiveIngredient {release: $r, rxcui: $c})
                            OPTIONAL MATCH (i)-[:BELONGS_TO_CLASS]->(k:DrugClass)
                            OPTIONAL MATCH (i)-[:CONTRAINDICATED_FOR]->(m:MedicalCondition)
                            RETURN i, collect(DISTINCT {name: k.name, type: k.type}) AS classes,
                                   collect(DISTINCT m.name) AS ci""", r=self.release["id"], c=res.rxcui).single()
        i = dict(rec["i"])
        out["drug"] = {"rxcui": i["rxcui"], "name": i["name"], "brands": i["brands"][:15],
                       "classes": [c for c in rec["classes"] if c["name"]], "medrt_ci_with": sorted(rec["ci"])}
        return out

    def neighborhood(self, rxcuis: list[str]) -> dict:
        """Nodes/edges for the drug relationship visualization (reference data only)."""
        self._ensure_index()
        with self.driver.session() as db:
            rows = db.run("""MATCH (i:ActiveIngredient {release: $r}) WHERE i.rxcui IN $c
                             OPTIONAL MATCH (i)-[:BELONGS_TO_CLASS]->(k:DrugClass {type: 'EPC'})
                             OPTIONAL MATCH (i)-[:INTERACTS_WITH]->(x:Interaction)<-[:INTERACTS_WITH]-(j:ActiveIngredient)
                             WHERE j.rxcui IN $c AND j <> i
                             RETURN i.rxcui AS rxcui, i.name AS name, collect(DISTINCT k.name) AS classes,
                                    collect(DISTINCT {id: x.id, other: j.rxcui, severity: x.severity}) AS ix""",
                          r=self.release["id"], c=rxcuis)
            nodes, edges = [], []
            for row in rows:
                nodes.append({"id": row["rxcui"], "label": row["name"], "kind": "drug"})
                for cls in row["classes"]:
                    if cls:
                        nodes.append({"id": f"class:{cls}", "label": cls, "kind": "class"})
                        edges.append({"source": row["rxcui"], "target": f"class:{cls}", "kind": "BELONGS_TO_CLASS"})
                for x in row["ix"]:
                    if x["id"] and row["rxcui"] < x["other"]:
                        edges.append({"source": row["rxcui"], "target": x["other"], "kind": "INTERACTS_WITH",
                                      "severity": x["severity"], "id": x["id"]})
        uniq = {n["id"]: n for n in nodes}
        return {"nodes": list(uniq.values()), "edges": edges}

    # ---------------------------------------------------------------- screening
    def screen(self, medications: list[dict], allergies: list[dict], conditions: list[dict],
               context: dict | None = None) -> dict:
        """medications: [{text, ref, source_kind, certainty, category, reconciliation}]
        allergies: [{substance, ref, verification_status, source_kind}]; conditions: [{name, ref, clinical_status}]"""
        self._ensure_index()
        release = self.release["id"]
        meds = []
        for m in medications:
            parts = components(m["text"])
            for part in parts:
                res = self.resolve(part)
                meds.append({**m, "resolution": res.as_dict(),
                             **({"component_of": m["text"]} if len(parts) > 1 else {})})
        resolved = [m for m in meds if m["resolution"]["rxcui"]]
        rx = {m["resolution"]["rxcui"] for m in resolved}
        alerts: list[dict] = []

        def depends(*facts: dict) -> list[str]:
            reasons = []
            for f in facts:
                if f.get("resolution", {}).get("match_type") == "suggestion":
                    reasons.append(f"medication '{f['text']}' identified only by similarity to {f['resolution']['name']}")
                if f.get("certainty") in ("uncertain", "inferred"):
                    reasons.append(f"'{f.get('text') or f.get('substance')}' is uncertain")
                if f.get("source_kind") in ("patient_reported", "unconfirmed"):
                    reasons.append(f"'{f.get('text') or f.get('substance')}' is patient-reported/unconfirmed")
                if f.get("verification_status") in ("unconfirmed", "presumed"):
                    reasons.append(f"allergy '{f.get('substance')}' is unconfirmed")
            return reasons

        with self.driver.session() as db:
            # Interactions among every pair of resolved medications.
            ix_rows = db.run("""MATCH (a:ActiveIngredient {release: $r})-[:INTERACTS_WITH]->(x:Interaction)
                                      <-[:INTERACTS_WITH]-(b:ActiveIngredient {release: $r})
                                WHERE a.rxcui IN $c AND b.rxcui IN $c AND a.rxcui < b.rxcui
                                OPTIONAL MATCH (x)-[s:SUPPORTED_BY]->(e:EvidenceSource)
                                RETURN a.rxcui AS a, b.rxcui AS b, x, collect({quote: s.quote, title: e.title,
                                       url: e.url, section: e.section, set_id: e.set_id, version: e.version,
                                       effective_time: e.effective_time}) AS ev""", r=release, c=list(rx)).data()
            by_rx: dict[str, list[dict]] = {}
            for m in resolved:
                by_rx.setdefault(m["resolution"]["rxcui"], []).append(m)
            for row in ix_rows:
                for ma in by_rx[row["a"]]:
                    for mb in by_rx[row["b"]]:
                        x = row["x"]
                        dep = depends(ma, mb)
                        severity, summary, dose_check = x["severity"], x["summary"], None
                        limit = json.loads(x["dose_limit"]) if x.get("dose_limit") else None
                        if limit:
                            limited = next(m for m in (ma, mb) if m["resolution"]["name"] == limit["drug"])
                            mg = daily_mg(limited)
                            dose_check = {"drug": limit["drug"], "max_daily_mg": limit["max_daily_mg"], "recorded_daily_mg": mg}
                            if mg is None:
                                dep = [*dep, f"the {limit['drug']} dose or frequency is not recorded"]
                                summary += f" Check the {limit['drug']} dose against the label limit."
                            elif mg > limit["max_daily_mg"]:
                                summary += (f" Recorded {limit['drug']} {mg:g} mg/day exceeds the label limit of "
                                            f"{limit['max_daily_mg']} mg/day.")
                            else:
                                severity = "caution"
                                summary += (f" Recorded {limit['drug']} {mg:g} mg/day is within the label limit of "
                                            f"{limit['max_daily_mg']} mg/day; monitor.")
                        alerts.append({"kind": "interaction", "id": x["id"], "severity": severity,
                                       "summary": summary, "drugs": [ma["resolution"]["name"], mb["resolution"]["name"]],
                                       "patient_facts": [_fact(ma), _fact(mb)], "dose_check": dose_check,
                                       "evidence": row["ev"], "status": x["status"],
                                       "confidence": "possible" if dep else "established", "depends_on": dep})
            # Allergies: map each recorded allergy term to the substance node, then to classes/ingredients.
            for allergy in allergies:
                term = norm(allergy["substance"])
                rows = db.run("""MATCH (a:AllergySubstance {release: $r}) WHERE any(t IN a.terms WHERE t = $t OR $t CONTAINS t)
                                 MATCH (a)-[rel:CONTRAINDICATED_FOR|CAUTION_WITH]->(target)
                                 MATCH (i:ActiveIngredient {release: $r}) WHERE i.rxcui IN $c AND
                                       (i = target OR (i)-[:BELONGS_TO_CLASS]->(target))
                                 OPTIONAL MATCH (a)-[s:SUPPORTED_BY]->(e:EvidenceSource)
                                 WHERE s.for_target IS NULL OR s.for_target = target.name
                                 RETURN a.id AS allergen, type(rel) AS rel, rel.basis AS basis, target.name AS target,
                                        i.rxcui AS rxcui, collect(DISTINCT {quote: s.quote, title: e.title, url: e.url,
                                        section: e.section, set_id: e.set_id, version: e.version,
                                        effective_time: e.effective_time}) AS ev""",
                              r=release, t=term, c=list(rx)).data()
                for row in rows:
                    for m in by_rx.get(row["rxcui"], []):
                        dep = depends(m, allergy)
                        caution = row["rel"] == "CAUTION_WITH"
                        alerts.append({
                            "kind": "allergy_caution" if caution else "allergy", "id": f"{row['allergen']}:{row['rxcui']}",
                            "severity": "caution" if caution else "contraindicated",
                            "summary": (f"Recorded allergy to {allergy['substance']}; {m['resolution']['name']} "
                                        f"{'may cross-react' if caution else 'is in the same group'} ({row['target']}; {row['basis']})."),
                            "drugs": [m["resolution"]["name"]],
                            "patient_facts": [{"text": allergy["substance"], "ref": allergy.get("ref"),
                                               "source_kind": allergy.get("source_kind")}, _fact(m)],
                            "evidence": [e for e in row["ev"] if e.get("title")] or
                                        [{"basis": row["basis"], "title": "FDA Established Pharmacologic Class membership"}],
                            "status": "demo_curated_not_clinically_reviewed",
                            "confidence": "possible" if dep else "established", "depends_on": dep})
            # Curated drug-condition cautions (label-quoted), e.g. NSAIDs in heart failure.
            cc_rows = db.run("""MATCH (i:ActiveIngredient {release: $r})-[:CAUTION_IN_CONDITION]->(x:ConditionCaution)
                                WHERE i.rxcui IN $c
                                OPTIONAL MATCH (x)-[s:SUPPORTED_BY]->(e:EvidenceSource)
                                RETURN i.rxcui AS rxcui, x, collect({quote: s.quote, title: e.title, url: e.url,
                                       section: e.section, set_id: e.set_id, version: e.version,
                                       effective_time: e.effective_time}) AS ev""", r=release, c=list(rx)).data()
            active_conditions = [c for c in conditions if c.get("clinical_status") in (None, "active", "recurrence", "relapse")]
            for row in cc_rows:
                x = row["x"]
                for cond in active_conditions:
                    if not any(t in norm(cond["name"]) for t in x["condition_terms"]):
                        continue
                    for m in by_rx.get(row["rxcui"], []):
                        dep = depends(m)
                        alerts.append({"kind": "condition_caution", "id": x["id"], "severity": x["severity"],
                                       "summary": f"{x['summary']} The record shows '{cond['name']}'.",
                                       "drugs": [m["resolution"]["name"]],
                                       "patient_facts": [{"text": cond["name"], "ref": cond.get("ref")}, _fact(m)],
                                       "evidence": row["ev"], "status": x["status"],
                                       "confidence": "possible" if dep else "established", "depends_on": dep})
            # Allergies the cardiac knowledge scope cannot screen (e.g. a penicillin allergy) are stated, not ignored.
            allergen_terms = [t for row in db.run("MATCH (a:AllergySubstance {release: $r}) RETURN a.terms AS t", r=release)
                              for t in row["t"]]
            out_of_scope = [a["substance"] for a in allergies
                            if not any(t == norm(a["substance"]) or t in norm(a["substance"]) for t in allergen_terms)]
            # MED-RT condition relationships: reference data only, always a non-blocking caution.
            aliases = self.release.get("condition_aliases", {})
            for cond in conditions:
                if cond.get("clinical_status") not in (None, "active", "recurrence", "relapse"):
                    continue
                targets = [(cond["name"], "exact")] + [tuple(a) for a in aliases.get(cond["name"].lower(), [])]
                targets = list({t[0].lower(): t for t in reversed(targets)}.values())  # exact wins over alias
                for target, match in targets:
                    rows = db.run("""MATCH (i:ActiveIngredient {release: $r})-[rel:CONTRAINDICATED_FOR]->(m:MedicalCondition {release: $r})
                                     WHERE i.rxcui IN $c AND toLower(m.name) = toLower($t)
                                     RETURN i.rxcui AS rxcui, m.name AS cond, rel.source_version AS v""",
                                  r=release, c=list(rx), t=target).data()
                    for row in rows:
                        for m in by_rx.get(row["rxcui"], []):
                            alerts.append({
                                "kind": "condition_contraindication", "id": f"medrt:{row['rxcui']}:{row['cond']}",
                                "severity": "caution",
                                "summary": (f"MED-RT links {m['resolution']['name']} with a contraindication context of "
                                            f"'{row['cond']}'; the record shows '{cond['name']}'"
                                            + ("" if match == "exact" else " (broader term)")
                                            + ". Check the label for the specific circumstance."),
                                "drugs": [m["resolution"]["name"]],
                                "patient_facts": [{"text": cond["name"], "ref": cond.get("ref")}, _fact(m)],
                                "evidence": [{"title": f"MED-RT ci_with ({row['v']})", "url": "https://lhncbc.nlm.nih.gov/RxNav/applications/RxClassIntro.html",
                                              "section": "MED-RT relationship"}],
                                "status": "reference_relationship", "match": match,
                                "confidence": "possible" if (match != "exact" or depends(m)) else "established",
                                "depends_on": depends(m)})

        unique: dict[str, dict] = {}
        for a in alerts:
            unique.setdefault(f"{a['kind']}|{a['id']}|{'|'.join(f['text'] for f in a['patient_facts'])}", a)
        contraindicated = {(a['patient_facts'][0]['text'], a['drugs'][0]) for a in unique.values() if a['kind'] == 'allergy'}
        alerts = [a for a in unique.values()
                  if not (a['kind'] == 'allergy_caution' and (a['patient_facts'][0]['text'], a['drugs'][0]) in contraindicated)]
        alerts.sort(key=lambda a: (SEVERITY_RANK.get(a["severity"], 9), a["confidence"] != "established"))
        # One alert per finding: the same drug can be both recorded and reported today. Keep the best-supported
        # version and list every patient fact behind it.
        merged: dict[tuple, dict] = {}
        for a in alerts:
            key = (a["kind"], a["id"])
            if key not in merged:
                merged[key] = {**a, "patient_facts": list(a["patient_facts"])}
            else:
                seen = {f["text"] for f in merged[key]["patient_facts"]}
                merged[key]["patient_facts"] += [f for f in a["patient_facts"] if f["text"] not in seen]
        alerts = list(merged.values())
        reasons = []
        if not medications:
            reasons.append("No current medications are recorded or reported; interaction screening could not run.")
        for m in meds:
            r = m["resolution"]
            if r["match_type"] == "unresolved":
                reasons.append(f"'{m['text']}' could not be identified; it was not screened.")
            elif r["match_type"] == "suggestion":
                reasons.append(f"'{m['text']}' was matched only by similarity ({r['name']}); confirm the medication.")
            if m.get("certainty") == "uncertain":
                reasons.append(f"'{m['text']}' is uncertain (patient unsure).")
        ctx = context or {}
        if ctx.get("reconciliation") == "none_recorded":
            reasons.append("No current medications are recorded in the chart; screening covers only medications "
                           "reported today.")
        elif ctx.get("reconciliation") in ("stale", "never_reconciled"):
            reasons.append(f"The recorded medication list is {ctx['reconciliation'].replace('_', ' ')}.")
        for a in allergies:
            if a.get("verification_status") in ("unconfirmed", "presumed"):
                reasons.append(f"Allergy '{a['substance']}' is unconfirmed.")
        for substance in dict.fromkeys(out_of_scope):
            reasons.append(f"Allergy '{substance}' is outside the cardiology knowledge scope and was not screened.")
        if ctx.get("allergy_conflict"):
            reasons.append("Allergy records conflict; allergy screening depends on resolving the conflict.")
        reasons.append(f"Coverage is limited to knowledge release {release} ({self.release.get('ingredients', '?')} "
                       f"{self.release.get('scope', '')} ingredients; demo-curated interactions, not a comprehensive "
                       "database).".replace("  ", " "))
        return {
            "release": release, "sources": self.release.get("sources", []),
            "medications": meds, "alerts": alerts,
            # Never "complete": at best the inputs were complete and only the reference coverage is limited.
            "completeness": {"status": "incomplete" if len(reasons) > 1 else "limited_coverage", "reasons": reasons},
            "statement": ("Screening found the alerts listed." if alerts else
                          "No alert was found among the identified medications in this knowledge release.")
                         + " This does not mean the combination is safe; screening is incomplete for the reasons listed.",
        }
