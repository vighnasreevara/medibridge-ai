"""Knowledge build verification (offline) and the deterministic medication-safety screen (live Neo4j)."""
from __future__ import annotations

import copy
import json

import pytest

from medibridge_knowledge import load
from medibridge_knowledge.fetch import ROOT

BUNDLE = ROOT / "build" / "bundle.json"
CURATED = ROOT / "curated" / "cardiac_curated.json"
needs_bundle = pytest.mark.skipif(not BUNDLE.exists(), reason="run python -m medibridge_knowledge.fetch first")


def _bundle():
    return json.loads(BUNDLE.read_text(encoding="utf-8"))


def _curated():
    return json.loads(CURATED.read_text(encoding="utf-8"))


@needs_bundle
def test_curated_set_verifies_against_labels():
    _, problems = load.verify(_bundle(), _curated())
    assert problems == []


@needs_bundle
def test_a_quote_not_in_the_label_is_rejected():
    bad = copy.deepcopy(_curated())
    bad["interactions"][0]["evidence"][0]["quote"] = "Warfarin is completely safe with every NSAID."
    _, problems = load.verify(_bundle(), bad)
    assert any("quote not found" in p for p in problems)


@needs_bundle
def test_unknown_drug_is_rejected():
    curated = _curated()
    curated["interactions"][0]["drugs"] = ["warfarin", "madeupzolam"]
    _, problems = load.verify(_bundle(), curated)
    assert any("unknown drug" in p for p in problems)


class _FakeSession:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **kw):
        store = self.store

        class Result:
            def single(self_inner):
                if "RETURN k.id AS id" in query:
                    return {"id": store["active"]}
                if "RETURN k" in query:
                    return {"k": {"id": store["active"], "sources": "[]", "counts": "{}", "condition_aliases": "{}"}}
                return None

            def __iter__(self_inner):
                store["index_loads"] += 1
                return iter([{"rxcui": "1", "name": "warfarin", "syn": [], "brands": ["Coumadin"]}])

        return Result()


class _FakeDriver:
    def __init__(self):
        self.store = {"active": "kr-old", "index_loads": 0}

    def session(self):
        return _FakeSession(self.store)


def test_knowledge_client_follows_release_switches():
    from medibridge_knowledge.graph import KnowledgeGraph

    driver = _FakeDriver()
    kg = KnowledgeGraph(driver)
    kg._ensure_index()
    kg._ensure_index()
    assert kg.release["id"] == "kr-old" and driver.store["index_loads"] == 1  # cached while unchanged
    driver.store["active"] = "kr-new"  # a new build switched the active release
    kg._ensure_index()
    assert kg.release["id"] == "kr-new" and driver.store["index_loads"] == 2


@pytest.fixture(scope="module")
def kg():
    from medibridge_api.services.knowledge import KnowledgeUnavailable, safe_graph

    try:
        return safe_graph()
    except KnowledgeUnavailable:
        pytest.skip("Neo4j knowledge release not loaded")


def meds(*names, source="clinician_documented", dose=None, freq=None):
    return [{"text": n, "source_kind": source, "dose_text": dose, "frequency_text": freq} for n in names]


def test_curated_dose_limits_name_a_drug_of_the_interaction():
    bad = copy.deepcopy(_curated())
    item = next(i for i in bad["interactions"] if i.get("dose_limit"))
    item["dose_limit"]["drug"] = "aspirin"
    if BUNDLE.exists():
        _, problems = load.verify(_bundle(), bad)
        assert any("dose limit" in p for p in problems)


@pytest.mark.live
def test_warfarin_nsaid_alert_with_label_evidence(kg):
    out = kg.screen(meds("Warfarin sodium 5 mg oral tablet") + meds("ibuprofen", source="patient_reported"), [], [])
    alert = next(a for a in out["alerts"] if a["kind"] == "interaction")
    assert set(alert["drugs"]) == {"warfarin", "ibuprofen"} and alert["severity"] == "major"
    assert alert["evidence"] and all(e["url"].startswith("https://dailymed") for e in alert["evidence"])
    assert alert["confidence"] == "possible"  # ibuprofen is only patient-reported


@pytest.mark.live
def test_same_drug_recorded_and_reported_gives_one_alert(kg):
    out = kg.screen(meds("Warfarin sodium 5 mg oral tablet") + meds("warfarin", "ibuprofen", source="patient_reported"), [], [])
    interactions = [a for a in out["alerts"] if a["kind"] == "interaction"]
    assert len(interactions) == 1
    assert {f["text"] for f in interactions[0]["patient_facts"]} >= {"Warfarin sodium 5 mg oral tablet", "warfarin", "ibuprofen"}


@pytest.mark.live
@pytest.mark.parametrize("dose,severity,phrase", [("40 mg", "major", "exceeds the label limit"),
                                                 ("20 mg", "caution", "within the label limit"),
                                                 (None, "major", "Check the simvastatin dose")])
def test_simvastatin_dose_limit_uses_the_recorded_dose(kg, dose, severity, phrase):
    out = kg.screen(meds("Simvastatin oral tablet", dose=dose, freq="once daily" if dose else None)
                    + meds("amLODIPine 2.5 MG Oral Tablet", dose="2.5 mg", freq="once daily"), [], [])
    alert = next(a for a in out["alerts"] if a["id"] == "ddi-simvastatin-amlodipine")
    assert alert["severity"] == severity and phrase in alert["summary"]


@pytest.mark.live
def test_brand_name_resolves_and_nitrate_pde5_is_contraindicated(kg):
    out = kg.screen(meds("Nitroglycerin 0.4 mg per spray mucosal spray") + meds("Viagra", source="patient_reported"), [], [])
    alert = next(a for a in out["alerts"] if a["kind"] == "interaction")
    assert alert["severity"] == "contraindicated" and set(alert["drugs"]) == {"nitroglycerin", "sildenafil"}


@pytest.mark.live
def test_nsaid_in_heart_failure_is_a_curated_condition_caution(kg):
    out = kg.screen(meds("Naproxen sodium 220 MG Oral Tablet"), [],
                    [{"name": "Chronic congestive heart failure", "clinical_status": "active"}])
    alert = next(a for a in out["alerts"] if a["kind"] == "condition_caution")
    assert alert["severity"] == "major" and alert["evidence"][0]["quote"]


@pytest.mark.live
def test_medrt_condition_links_never_block(kg):
    # MED-RT lists heart failure for beta-blockers that treat it; that must not become a contraindication.
    out = kg.screen(meds("carvedilol 25 MG Oral Tablet"), [], [{"name": "Chronic congestive heart failure",
                                                               "clinical_status": "active"}])
    medrt = [a for a in out["alerts"] if a["kind"] == "condition_contraindication"]
    assert medrt and all(a["severity"] == "caution" for a in medrt)


@pytest.mark.live
def test_combination_product_is_split_into_ingredients(kg):
    out = kg.screen(meds("hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet"),
                    [{"substance": "Lisinopril", "verification_status": "confirmed", "source_kind": "clinician_documented"}], [])
    allergy = next(a for a in out["alerts"] if a["kind"] == "allergy")
    assert allergy["severity"] == "contraindicated"
    assert allergy["patient_facts"][1]["text"] == "hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet"


@pytest.mark.live
def test_allergy_outside_the_cardiac_scope_is_stated(kg):
    out = kg.screen(meds("aspirin 81 mg"), [{"substance": "Penicillin V", "verification_status": "confirmed"}], [])
    assert any("outside the cardiology knowledge scope" in r for r in out["completeness"]["reasons"])


@pytest.mark.live
def test_screen_never_claims_safety(kg):
    for out in (kg.screen(meds("amlodipine 5 mg tablet"), [], []), kg.screen([], [], [])):
        assert "does not mean the combination is safe" in out["statement"]
        assert out["completeness"]["status"] in ("incomplete", "limited_coverage")


@pytest.mark.live
def test_no_medications_means_screening_could_not_run(kg):
    out = kg.screen([], [], [])
    assert any("could not run" in r for r in out["completeness"]["reasons"])


@pytest.mark.live
def test_misheard_drug_is_screened_as_possible(kg):
    out = kg.screen(meds("Simvastatin 20 mg oral tablet", dose="20 mg", freq="once daily")
                    + meds("Chlorithromycin", source="patient_reported"), [], [])
    alert = next(a for a in out["alerts"] if a["kind"] == "interaction")
    assert alert["severity"] == "contraindicated" and alert["confidence"] == "possible"
    assert any("similarity" in d for d in alert["depends_on"])
