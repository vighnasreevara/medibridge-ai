"""Live acceptance scenario against the running stack (real ASR, real local LLMs, real stores).

    scripts/start.ps1 ; uv run python -m tests.ai_eval.acceptance_live [--scenario af_warfarin_ibuprofen] [--override]

Drives the HTTP API exactly as the UI does, using the generated demo accounts. Writes var/eval/acceptance.json
with the timings and outcomes of every step. Requires a freshly seeded database (today's encounter must be open).
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx

from medibridge_api.config import REPO_ROOT

API = "http://127.0.0.1:8000"


def accounts() -> dict[str, tuple[str, str]]:
    out = {}
    for line in (REPO_ROOT / "var" / "demo-credentials.txt").read_text(encoding="utf-8").splitlines()[2:]:
        parts = [p for p in line.split("  ") if p.strip()]
        if len(parts) >= 4:
            out.setdefault(parts[0].strip(), (parts[2].strip(), parts[3].strip()))
    return out


def client() -> httpx.Client:
    return httpx.Client(base_url=API, headers={"X-MediBridge-Request": "1"}, timeout=120, trust_env=False)


def login(c: httpx.Client, who: tuple[str, str]) -> dict:
    r = c.post("/api/auth/login", json={"email": who[0], "password": who[1]})
    r.raise_for_status()
    return r.json()


def wait(c: httpx.Client, encounter_id: str, gate: str | None, done: set[str] = frozenset(), timeout: int = 1800) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        runs = c.get(f"/api/encounters/{encounter_id}/runs").json()["items"]
        run = runs[0] if runs else None
        if run and ((gate and run["waiting_gate"] == gate) or run["status"] in done):
            return run
        if run and run["status"] in ("failed", "blocked") and run["status"] not in done:
            raise RuntimeError(f"run {run['status']}: {run['error_code']} at {run['current_node']}")
        time.sleep(3)
    raise TimeoutError(gate)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="af_warfarin_ibuprofen")
    parser.add_argument("--override", action="store_true", help="override blocking order alerts instead of revising")
    args = parser.parse_args()
    script = next(s for s in json.loads((REPO_ROOT / "tests/fixtures/intake_scripts.json").read_text(encoding="utf-8"))["scripts"]
                  if s["key"] == args.scenario)
    manifest = json.loads((REPO_ROOT / "var" / "seed-manifest.json").read_text(encoding="utf-8"))
    scen = next(s for s in manifest["scenarios"] if s["key"] == script["scenario"])
    acc = accounts()
    nurse, doctor = acc["Nurse / Intake Operator"], acc["Doctor"]
    report: dict = {"scenario": args.scenario, "steps": []}

    def step(name: str, t0: float, **info):
        report["steps"].append({"step": name, "seconds": round(time.time() - t0, 1), **info})
        print(f"[{time.time() - T0:7.1f}s] {name} ({time.time() - t0:.1f}s) {info if info else ''}", flush=True)

    T0 = time.time()
    n = client()
    login(n, nurse)
    d = client()
    doc_session = login(d, doctor)
    t = time.time()
    # Patient Registry (EMPI): a returning patient is matched by MRN + date of birth; a new one is registered.
    if scen.get("registration"):
        reg = scen["registration"]
        r = n.post("/api/registry/register", json={**{k: reg[k] for k in ("given_name", "family_name", "birth_date",
                                                                             "sex_at_birth")},
                                                   "attending_user_id": doc_session["id"], "confirm_new": True})
        r.raise_for_status()
        patient = r.json()["patient"]
        enc = n.post("/api/encounters", json={"patient_id": patient["patient_id"], "attending_user_id": doc_session["id"],
                                              "chief_complaint": reg["chief_complaint"]})
        enc.raise_for_status()
        eid = enc.json()["id"]
        step("patient registered (EMPI) and encounter created", t, mrn=patient["mrn"])
    else:
        r = n.post("/api/registry/match", json={"mrn": scen["mrn"], "birth_date": scen["birth_date"]})
        r.raise_for_status()
        assert r.json()["status"] == "matched", r.json()
        eid = scen["demo_encounter_id"]
        step("returning patient matched (EMPI, MRN + date of birth)", t, mrn=scen["mrn"])
    enc = n.get(f"/api/encounters/{eid}").json()
    t = time.time()
    n.post(f"/api/encounters/{eid}/verify-identity", json={"method": "wristband+dob", "mrn": enc["patient"]["mrn"],
                                                           "birth_date": enc["patient"]["birth_date"]}).raise_for_status()
    for ct in ("audio_recording", "ai_processing"):
        n.post(f"/api/encounters/{eid}/consents", json={"consent_type": ct, "status": "granted",
                                                        "method": "verbal_witnessed"}).raise_for_status()
    step("identity verified + consent recorded", t)
    t = time.time()
    audio = Path(REPO_ROOT / "var" / "fixtures" / "audio" / f"{args.scenario}.wav").read_bytes()
    up = n.post(f"/api/encounters/{eid}/intake/audio", files={"file": (f"{args.scenario}.wav", audio, "audio/wav")})
    if up.status_code != 201:
        raise RuntimeError(f"audio upload failed: {up.status_code} {up.text}")
    tr = up.json()
    resp = n.post(f"/api/encounters/{eid}/runs", json={"transcription_id": tr["transcription_id"],
                                                       "idempotency_key": uuid.uuid4().hex})
    if resp.status_code == 409 and resp.json()["detail"].get("code") == "run_active":
        n.post(f"/api/runs/{resp.json()['detail']['run_id']}/cancel",
               json={"reason": "Leftover run from an earlier attempt"}).raise_for_status()
        resp = n.post(f"/api/encounters/{eid}/runs", json={"transcription_id": tr["transcription_id"],
                                                           "idempotency_key": uuid.uuid4().hex})
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"run start failed: {resp.status_code} {resp.text}")
    run = resp.json()
    step("audio uploaded, run started", t, run_id=run["id"])
    t = time.time()
    wait(n, eid, "transcript_review")
    raw = n.get(f"/api/transcriptions/{tr['transcription_id']}").json()["raw_segments"]
    step("Ambient Scribe transcribed (local Whisper)", t, segments=len(raw), uncertain=sum(s["uncertain"] for s in raw))
    # Operator review: attribute speakers by matching the script turns; keep the ASR text (no silent correction).
    clin = [x[1].lower() for x in script["turns"] if x[0] == "clinician"]

    def speaker(text: str) -> str:
        low = text.lower().strip()
        has_clinician = any(c[:14] in low for c in clin)
        rest = low
        for c in clin:
            rest = rest.replace(c.rstrip("?."), "")
        if has_clinician and len(rest.strip(" ?.,")) > 15:
            return "unattributed"  # Whisper merged both speakers into one segment
        return "clinician" if has_clinician else "patient"

    segs = [{"id": s["id"], "text": s["text"], "speaker": speaker(s["text"])} for s in raw]
    report["speakers"] = [s["speaker"] for s in segs]
    t = time.time()
    n.post(f"/api/transcriptions/{tr['transcription_id']}/review", json={"confirm": True, "segments": segs}).raise_for_status()
    wait(n, eid, "handoff_review")
    step("extraction + chart + evidence retrieval + medication screen + handoff brief", t)
    h = d.get(f"/api/encounters/{eid}/handoff").json()
    sections = {s["key"]: s for s in h["content"]["sections"]}
    report["handoff"] = {"version": h["version"], "sections": len(sections), "format": h["content"].get("format"),
                         "preflight": {c["key"]: c["status"] for c in h["content"].get("preflight", [])},
                         "red_flags": [i["text"] for i in sections["red_flags"]["items"]],
                         "alerts": [i["text"] for i in sections["medication_alerts"]["items"]],
                         "symptoms": [i["text"] for i in sections["current_symptoms"]["items"]],
                         "results": len(sections["results"]["items"]),
                         "questions": [i["text"] for i in sections["unresolved_questions"]["items"]],
                         "timeline": [i["text"] for i in sections["symptom_timeline"]["items"]],
                         "generation": h["content"].get("generation"), "privacy": h["validation"].get("privacy_removed")}
    t = time.time()
    d.post(f"/api/encounters/{eid}/handoff/decision", json={"version": h["version"], "expected_sha256": h["content_sha256"],
                                                            "decision": "approved", "rationale": "Reviewed."}).raise_for_status()
    wait(d, eid, "orders_review")
    draft = d.get(f"/api/encounters/{eid}/documentation").json()["draft"]
    step("doctor approved the brief; Order Entry drafted the note", t, subjective_source=draft["subjective_source"])

    def submit(orders: list[dict]) -> dict:
        body = {"subjective": draft["subjective"], "objective": draft["objective"],
                "assessment": "Synthetic demo assessment entered by the doctor.",
                "plan": "Stop the medicine that interacts. Review in clinic in 2 weeks.",
                "record_findings": [k for k in draft["suggested_findings"] if k.startswith("intake:symptom")][:3],
                "prescriptions": orders}
        prep = d.post(f"/api/encounters/{eid}/documentation/prepare", json=body)
        prep.raise_for_status()
        cs = prep.json()
        d.post(f"/api/encounters/{eid}/documentation/submit", json={"changeset_id": cs["changeset_id"],
                                                                    "expected_sha256": cs["content_sha256"]}).raise_for_status()
        return cs

    orders = script.get("orders") or {}
    t = time.time()
    cs = submit(orders.get("propose", []))
    run = wait(d, eid, "safety_review", done={"completed"})
    screen = d.get(f"/api/encounters/{eid}/documentation").json()["changeset"]["safety_screen"]
    report["order_screen"] = {"status": screen["status"], "blocking": screen["blocking"],
                              "alerts": [a["summary"] for a in screen["alerts"]]}
    step("orders screened by Medication Safety (agent to agent)", t, status=screen["status"], blocking=screen["blocking"])
    if run["waiting_gate"] == "safety_review":
        t = time.time()
        if args.override or not orders.get("revise_to"):
            blocking = [a["id"] for a in screen["alerts"] if a["severity"] in ("contraindicated", "major")]
            d.post(f"/api/encounters/{eid}/documentation/safety-decision",
                   json={"changeset_id": cs["changeset_id"], "action": "override", "acknowledged_alerts": blocking,
                         "reason": "Synthetic demo override after reviewing the alert."}).raise_for_status()
            step("doctor overrode the alert with a reason", t)
        else:
            d.post(f"/api/encounters/{eid}/documentation/safety-decision",
                   json={"changeset_id": cs["changeset_id"], "action": "revise"}).raise_for_status()
            wait(d, eid, "orders_review")
            submit(orders["revise_to"])
            run = wait(d, eid, "safety_review", done={"completed"})
            screen2 = d.get(f"/api/encounters/{eid}/documentation").json()["changeset"]["safety_screen"]
            report["revised_order_screen"] = {"status": screen2["status"], "blocking": screen2["blocking"]}
            step("doctor revised the orders; re-screened", t, status=screen2["status"])
    t = time.time()
    final_run = wait(d, eid, None, done={"completed"})
    avs = d.get(f"/api/encounters/{eid}/after-visit-summary").json()["avs"]
    report["avs"] = {"status": avs["status"] if avs else None,
                     "medicines": [m["plain"] for m in avs["content"]["medicines"]] if avs else [],
                     "next_steps_source": avs["content"]["next_steps"]["source"] if avs else None}
    enc_after = d.get(f"/api/encounters/{eid}").json()
    report["final"] = {"run_status": final_run["status"], "encounter_status": enc_after["status"]}
    step("committed by the Chart agent; after-visit summary drafted", t, **report["final"])
    report["total_seconds"] = round(time.time() - T0, 1)
    out = REPO_ROOT / "var" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"acceptance_{args.scenario}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "steps"}, indent=1, default=str))
    return 0 if enc_after["status"] == "finalized" else 1


if __name__ == "__main__":
    raise SystemExit(main())
