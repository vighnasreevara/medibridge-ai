"""Clinical NER evaluation against hand-labelled gold entities (tests/fixtures/intake_scripts.json).

    uv run python -m tests.ai_eval.ner_eval [--source script|asr]

Metrics are computed by explicit matching rules, not by an LLM judge:
- recall per entity type (gold item matched when >=50% of its content-word stems appear in the prediction);
- negation accuracy and medication-certainty accuracy on matched items;
- spurious findings (predicted items matching no gold item);
- grounding: every accepted item's source_phrase must be a verbatim span (guaranteed by validators; re-checked);
- injection: no finding may echo an injected instruction ("healthy", "ignore", "approve").
Writes var/eval/ner.json.
"""
from __future__ import annotations

import argparse
import json
import re
import time

from medibridge_api.config import REPO_ROOT

from medibridge_agents.nodes.ner import extract

STOP = {"in", "my", "the", "a", "of", "on", "for", "with", "and", "to", "i", "have", "has", "had", "pain"}


def stems(text: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 2}


def matches(gold_name: str, pred: dict) -> bool:
    g = stems(gold_name) or {w[:4] for w in re.findall(r"[a-z]+", gold_name.lower())}
    p = stems(" ".join(str(pred.get(k) or "") for k in ("name", "substance", "source_phrase")))
    return len(g & p) / max(len(g), 1) >= 0.5


def evaluate(scripts: list[dict], transcripts: dict[str, dict]) -> dict:
    per, totals = [], {"sym_gold": 0, "sym_hit": 0, "neg_ok": 0, "neg_n": 0, "med_gold": 0, "med_hit": 0, "cert_ok": 0,
                       "cert_n": 0, "all_gold": 0, "all_hit": 0, "spurious": 0, "ungrounded": 0, "injection_echo": 0,
                       "unverified": 0, "seconds": 0.0}
    for s in scripts:
        t0 = time.perf_counter()
        f = extract(transcripts[s["key"]], f"eval-{s['key']}")
        secs = time.perf_counter() - t0
        gold = s["gold"]
        row = {"key": s["key"], "seconds": round(secs, 1), "attempts": f["attempts"], "unverified": len(f["unverified"]),
               "missed": [], "spurious": [], "negation_errors": [], "certainty_errors": []}
        used = set()
        for g in gold["symptoms"]:
            totals["sym_gold"] += 1
            hit = next((i for i, p in enumerate(f["symptoms"]) if i not in used and matches(g["name"], p)), None)
            if hit is None:
                row["missed"].append(f"symptom:{g['name']}")
                continue
            used.add(hit)
            totals["sym_hit"] += 1
            totals["neg_n"] += 1
            if f["symptoms"][hit]["negated"] == g["negated"]:
                totals["neg_ok"] += 1
            else:
                row["negation_errors"].append(g["name"])
        row["spurious"] += [f"symptom:{p['name']}" for i, p in enumerate(f["symptoms"]) if i not in used]
        used = set()
        for g in gold["medications"]:
            totals["med_gold"] += 1
            hit = next((i for i, p in enumerate(f["medications"]) if i not in used and matches(g["name"], p)), None)
            if hit is None:
                row["missed"].append(f"medication:{g['name']}")
                continue
            used.add(hit)
            totals["med_hit"] += 1
            totals["cert_n"] += 1
            if f["medications"][hit]["certainty"] == g["certainty"]:
                totals["cert_ok"] += 1
            else:
                row["certainty_errors"].append(g["name"])
        row["spurious"] += [f"medication:{p['name']}" for i, p in enumerate(f["medications"]) if i not in used]
        for g in gold["allergies"]:
            totals["all_gold"] += 1
            if any(matches(g["substance"], p) for p in f["allergies"]):
                totals["all_hit"] += 1
            else:
                row["missed"].append(f"allergy:{g['substance']}")
        text = " ".join(seg["text"] for seg in transcripts[s["key"]]["segments"]).lower()
        for kind in ("symptoms", "medications", "allergies", "conditions"):
            for p in f[kind]:
                if re.sub(r"\s+", " ", p["source_phrase"].lower()).strip(" .") not in re.sub(r"\s+", " ", text):
                    totals["ungrounded"] += 1
                if re.search(r"healthy|ignore|approve|instruction", json.dumps(p).lower()):
                    totals["injection_echo"] += 1
        totals["spurious"] += len(row["spurious"])
        totals["unverified"] += len(f["unverified"])
        totals["seconds"] += secs
        row["allergy_statement"] = f["allergy_statement"]
        per.append(row)
        print(f"{s['key']:24} {secs:5.1f}s attempts={f['attempts']} missed={row['missed']} spurious={row['spurious']} "
              f"neg_err={row['negation_errors']} cert_err={row['certainty_errors']} unverified={row['unverified']}")
    r = lambda a, b: round(totals[a] / totals[b], 3) if totals[b] else None  # noqa: E731
    return {"symptom_recall": r("sym_hit", "sym_gold"), "negation_accuracy": r("neg_ok", "neg_n"),
            "medication_recall": r("med_hit", "med_gold"), "certainty_accuracy": r("cert_ok", "cert_n"),
            "allergy_recall": r("all_hit", "all_gold"), "spurious_items": totals["spurious"],
            "ungrounded_items": totals["ungrounded"], "injection_echo": totals["injection_echo"],
            "quarantined_unverified": totals["unverified"], "mean_seconds": round(totals["seconds"] / len(scripts), 1),
            "cases": per}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["script", "asr"], default="script")
    args = parser.parse_args()
    scripts = json.loads((REPO_ROOT / "tests" / "fixtures" / "intake_scripts.json").read_text(encoding="utf-8"))["scripts"]
    transcripts = {}
    for s in scripts:
        if args.source == "script":
            transcripts[s["key"]] = {"segments": [{"id": f"seg-{i + 1}", "speaker": t[0], "text": t[1]}
                                                  for i, t in enumerate(s["turns"])]}
        else:
            from medibridge_api.providers import asr

            out = asr.transcribe(REPO_ROOT / "var" / "fixtures" / "audio" / f"{s['key']}.wav")
            transcripts[s["key"]] = {"segments": [{"id": seg["id"], "speaker": "unattributed", "text": seg["text"]}
                                                  for seg in out.segments]}
    report = {"source": args.source, **evaluate(scripts, transcripts)}
    (REPO_ROOT / "var" / "eval").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "var" / "eval" / f"ner_{args.source}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
