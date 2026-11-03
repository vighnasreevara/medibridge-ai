"""Clinician-reviewed escalation rules (guardrail 11).

Rules are configuration (escalation_rules.json), evaluated deterministically and negation-aware on the
reviewed transcript. A matching rule raises a flag for clinician review; nothing in the system can lower or
clear it except an explicit clinician acknowledgement. Absence of a flag is never reported as "not serious".
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .negation import is_negated, sentences

RULES_FILE = Path(__file__).with_name("escalation_rules.json")


@lru_cache
def rules() -> dict:
    return json.loads(RULES_FILE.read_text(encoding="utf-8"))


def _affirmed(text: str, phrases: list[str]) -> tuple[str, str] | None:
    """First (phrase, sentence) present in the text and not negated in its sentence."""
    for sent in sentences(text):
        low = sent.lower()
        for p in phrases:
            if p in low and not is_negated(sent, p):
                return p, sent
    return None


def evaluate(text: str, segment_refs: list[tuple[str, str]] | None = None) -> list[dict]:
    """``segment_refs``: [(segment_id, text)] to attach the triggering transcript segment as evidence."""
    cfg = rules()
    flags = []
    for rule in cfg["rules"]:
        matched = []
        ok = True
        for group in rule.get("all_of", []):
            hit = _affirmed(text, group)
            if not hit:
                ok = False
                break
            matched.append(hit[0])
        if not ok:
            continue
        if rule.get("any_of"):
            hit = _affirmed(text, rule["any_of"])
            if not hit:
                continue
            matched.append(hit[0])
        refs = [sid for sid, seg in (segment_refs or []) if any(m in seg.lower() for m in matched)]
        flags.append({"rule_id": rule["id"], "level": rule["level"], "label": rule["label"],
                      "message": rule["message"], "matched_terms": matched,
                      "evidence_ids": [f"segment:{r}" for r in refs], "rules_version": cfg["version"],
                      "status": "open", "clearable_by": "clinician_only"})
    # A specific urgent rule supersedes the generic review rules it names ("supersedes" in the rules file).
    by_id = {r["id"]: r for r in cfg["rules"]}
    superseded = {x for f in flags for x in by_id[f["rule_id"]].get("supersedes", [])}
    return [f for f in flags if f["rule_id"] not in superseded]
