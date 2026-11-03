"""Deterministic agent components: negation, uncertainty, escalation rules and NER validators."""
from __future__ import annotations

import pytest

from medibridge_agents.escalation import evaluate
from medibridge_agents.negation import is_negated, is_uncertain
from medibridge_agents.nodes.ner import Extraction, Validator


@pytest.mark.parametrize("sentence,term,expected", [
    ("No fever.", "fever", True),
    ("I do not have a cough.", "cough", True),
    ("There is no numbness in my legs.", "numbness", True),
    ("Denies nausea or vomiting.", "vomiting", True),
    ("I have had a fever since last night.", "fever", False),
    ("Not much sleep but I have a headache.", "headache", False),  # termination word ends the negation scope
    ("I feel a bit short of breath and sweaty.", "short of breath", False),
    # Found in the phase 2 live run: "never" alone was not a cue, and a denied faint became an urgent red flag.
    ("No chest pain, and I have never fainted.", "fainted", True),
    ("I haven't passed out.", "passed out", True),
    ("I didn't faint but my heart was racing.", "racing", False),
    ("My breathing is fine, I am not short of breath.", "shortness of breath", True),
    ("I have never fainted.", "fainting", True),
    ("I get short of breath when I lie flat.", "shortness of breath", False),
])
def test_negation(sentence, term, expected):
    assert is_negated(sentence, term) is expected


def test_uncertainty_cues():
    assert is_uncertain("I think it is atorva-something, maybe forty milligrams")
    assert not is_uncertain("I take warfarin for my heart rhythm.")


def test_escalation_fires_on_chest_pain_red_flags_and_supersedes_generic_rule():
    flags = evaluate("The chest pain started two hours ago. It goes into my left arm. I feel short of breath.")
    assert [f["rule_id"] for f in flags] == ["chest_pain_red_flags"]
    assert flags[0]["level"] == "urgent" and flags[0]["clearable_by"] == "clinician_only"


@pytest.mark.parametrize("text,rule", [
    ("I get out of breath when I lie flat, I need three pillows now.", "heart_failure_decompensation"),
    ("I use my nitro spray. I took a Viagra last night.", "nitrate_with_pde5_inhibitor"),
    ("The lights look yellowish with halos. I take digoxin every morning.", "possible_digoxin_toxicity"),
    ("I take warfarin and I have noticed black stools.", "bleeding_on_anticoagulant"),
    ("My heart is racing and I feel dizzy.", "palpitations_with_dizziness"),
    ("I fainted in the kitchen yesterday.", "syncope"),
])
def test_cardiac_red_flags(text, rule):
    assert rule in [f["rule_id"] for f in evaluate(text)]


def test_cardiac_red_flags_respect_negation():
    assert evaluate("I have not had any chest pain and I have not fainted. My ankles are not swollen.") == []
    assert evaluate("No chest pain, and I have never fainted.") == []


def test_escalation_respects_negation():
    assert evaluate("I have had a cough for five days. I have no chest pain.") == []


def test_escalation_anticoagulant_with_nsaid():
    ids = [f["rule_id"] for f in evaluate("I take warfarin. I have been taking ibuprofen for my back.")]
    assert ids == ["anticoagulant_with_bleeding_risk"]


TRANSCRIPT = ("I have had a dry cough for five days. No fever. I take lisinopril 10 milligrams every morning. "
              "I think it's atorva-something for cholesterol.")


def _x(**kw):
    base = {"chief_complaint": None, "symptoms": [], "medications": [], "allergies": [], "known_conditions": [],
            "allergy_statement": "not_discussed", "allergy_statement_phrase": None}
    return Extraction.model_validate({**base, **kw})


def test_validator_rejects_ungrounded_phrases():
    v = Validator(TRANSCRIPT)
    x = _x(symptoms=[{"name": "chest pain", "source_phrase": "my chest hurts", "negated": False, "duration": None,
                      "onset": None, "severity": None, "body_site": None}])
    assert any("not an exact quote" in p for p in v(x))


def test_validator_rejects_dropped_negation_and_wrong_certainty():
    v = Validator(TRANSCRIPT)
    x = _x(symptoms=[{"name": "fever", "source_phrase": "No fever", "negated": False, "duration": None, "onset": None,
                      "severity": None, "body_site": None}],
           medications=[{"name": "atorva-something", "source_phrase": "I think it's atorva-something", "dose": None,
                         "frequency": None, "patient_unsure": False, "taking_now": "yes"},
                        {"name": "lisinopril", "source_phrase": "I take lisinopril 10 milligrams", "dose": "10 milligrams",
                         "frequency": None, "patient_unsure": True, "taking_now": "yes"}])
    problems = v(x)
    assert any("negated should be true" in p for p in problems)
    assert any("set patient_unsure to true" in p for p in problems)
    assert any("set patient_unsure to false" in p for p in problems)


def test_validator_rejects_silent_name_correction():
    v = Validator(TRANSCRIPT)
    x = _x(medications=[{"name": "atorvastatin", "source_phrase": "I think it's atorva-something", "dose": None,
                         "frequency": None, "patient_unsure": True, "taking_now": "yes"}])
    assert any("exactly as said" in p for p in v(x))


def test_validator_accepts_a_faithful_extraction():
    v = Validator(TRANSCRIPT)
    x = _x(symptoms=[{"name": "dry cough", "source_phrase": "I have had a dry cough for five days", "negated": False,
                      "duration": "five days", "onset": None, "severity": None, "body_site": None},
                     {"name": "fever", "source_phrase": "No fever", "negated": True, "duration": None, "onset": None,
                      "severity": None, "body_site": None}],
           medications=[{"name": "lisinopril", "source_phrase": "I take lisinopril 10 milligrams every morning",
                         "dose": "10 milligrams", "frequency": "every morning", "patient_unsure": False, "taking_now": "yes"}])
    assert v(x) == []
