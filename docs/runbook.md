# MediBridge AI runbook (local)

> Synthetic-data research and demo platform. **Not approved for clinical use.**

## Prerequisites

| Requirement | Verified version |
|---|---|
| Windows 11, PowerShell 5.1+ | 26200 |
| Docker Desktop (WSL2) | 4.36, Engine 27.3 |
| Python | 3.13 (via `uv`) |
| Node.js | 22.14 |
| uv | 0.11 |
| Ollama (system install) | 0.40.0 (`winget install --id Ollama.Ollama --version 0.40.0 --exact`) |
| Java | 17+ for generating the Synthea cohort once (`winget install --id Microsoft.OpenJDK.21 --exact`) |
| RAM | 32 GB recommended. The models use about 11 GB: qwen3.5:9b 6 GB, qwen3.5:4b 3 GB, bge-m3 1 GB, Whisper small.en about 0.5 GB |
| Disk | About 15 GB: models, images, the Python environment and node_modules |

## First-time setup

```powershell
& .\scripts\setup.ps1     # does all of the following:
#   .env with random secrets, Python + npm dependencies, Ollama models, data services, migrations
#   uv run python -m medibridge_seed.synthea generate   # Synthea 4.0.0 cardiac cohort (60 patients, ~2 min, Java)
#   uv run python -m medibridge_seed                    # import the cardiology part of each record + demo scenarios
#   uv run python -m medibridge_knowledge.fetch         # cardiology drug data from NLM RxNav + openFDA (cached)
#   uv run python -m medibridge_knowledge.load          # verify every curated label quote, batch-load into Neo4j
#   uv run python -m medibridge_api.services.rerank --download   # local re-ranker (~130 MB)
#   uv run python -m medibridge_api.cli ingest          # extract, chunk and embed the reports into Qdrant
#   scripts\make_synthetic_audio.ps1                    # synthetic intake recordings
```

Demo accounts are generated with random passwords at every seed and written to `var/demo-credentials.txt`. They are never committed or shown in the UI.

## Daily use

```powershell
& .\scripts\start.ps1    # data services + 8 agent services + API (127.0.0.1:8000) + web (127.0.0.1:5173), then prints health
& .\scripts\stop.ps1     # stops the web, the API and the agent services; data services stop too unless -KeepServices
```

Open http://127.0.0.1:5173. Every service binds to `127.0.0.1` only.

**Agent services.** The supervisor (LangGraph, the checkpoints and the three human gates) runs inside the API. The eight specialist agents run as separate processes, one per agent, each an A2A service on `127.0.0.1:8101`-`8108`. `start.ps1` starts them first and waits for each agent card. Their logs are in `var/logs/agent-<id>.err.log`. Together they use about 0.75 GB of RAM when idle. Mission control → Agent services shows each one's endpoint, skills and live status.

**Port conflicts.** MediBridge uses the default ports 5432, 6333/6334, 7474/7687, 8000, 8101-8108, 5173 and 11434. If another project's containers hold them, `start.ps1` stops and names the container. Stop it with `docker stop <name>`.

## Demo walkthrough (about 15 minutes, most of it local inference)

1. Sign in as the **nurse**. On **Intake**, use the Patient Registry: enter the MRN and date of birth of a patient on today's clinic list (or register a new patient, for example the `new_patient_registration` scenario in `var/seed-manifest.json`), then start today's intake. Verify identity and record both consents.
2. Record audio, or upload a file from `var/fixtures/audio/` (for example `af_warfarin_ibuprofen.wav` for the `af_anticoagulant_nsaid` patient).
3. At the transcript-review gate, check the highlighted words, assign speakers, and confirm.
4. Extraction, the chart, evidence retrieval, the medication screen and the brief run (5-10 minutes on CPU). Watch them in **Agent mission control**.
5. Sign in as a **doctor**. Open **Handoff briefs**: review the SBAR brief, its pre-generation checks, evidence chips, results and alerts, then approve.
6. Write the assessment and plan, add an order (for example aspirin 81 mg once daily for the warfarin patient), validate, then **sign and submit**. Medication Safety screens the orders; a blocking alert comes back to you: override with a reason, or revise (for example to acetaminophen) and submit again.
7. The Chart agent commits the record; the After-Visit Summary agent writes the patient's summary. Open the printable summary and **release** it. As the **admin**, the audit log shows every step and the chain verifies as intact.

## Operations

| Task | Command or place |
|---|---|
| Component health | `GET /api/health` or the Overview page; admins can run Administration → Diagnostics |
| Agent service status | `GET /api/agents/a2a`, Mission control → Agent services, or `GET http://127.0.0.1:810x/.well-known/agent-card.json` |
| Restart the agent services | `scripts\stop.ps1 -KeepServices` then `scripts\start.ps1` (data services keep running). A run that failed while an agent was down can be retried from Workflow & approvals |
| List agents and ports | `uv run python -m medibridge_agents.a2a --list` |
| Run agents inside the API (debugging) | Set `AGENT_TRANSPORT=inprocess` in `.env` and restart; health then shows a warning |
| Re-index documents (e.g. after an embedding-model change) | `uv run python -m medibridge_api.cli reindex` |
| Retry failed ingestions | `uv run python -m medibridge_api.cli ingest --retry-failed` |
| Verify the audit chain | `uv run python -m medibridge_api.cli verify-audit`, or Audit & governance |
| Resume a run interrupted by a restart | Workflow & approvals → Resume (it continues from the last checkpoint) |
| Abandon a stuck or interrupted run | Workflow & approvals → Cancel (requires a reason; audited) |
| Change local models | Administration → Configuration (installed, non-cloud models only; audited) |
| Rebuild knowledge after new source releases | `medibridge_knowledge.fetch` then `medibridge_knowledge.load`. A new release is loaded side by side and switched atomically; old releases are removed afterwards |
| Reset the synthetic dataset | `uv run python -m medibridge_seed --reset`, then `uv run python -m medibridge_api.cli ingest`. **Destroys** local data, drops the vector collection and generates new demo passwords |
| Regenerate the Synthea cohort | `uv run python -m medibridge_seed.synthea generate` (seeded and dated, so it is reproducible), then reset as above |
| Rebuild the cardiology knowledge | Edit `knowledge/sources/curated/build_cardiac_curated.py`, run it, then `medibridge_knowledge.load` (quotes are verified against the labels) |

## MCP server (read-only tools for a local MCP client)

```powershell
$env:MEDIBRIDGE_MCP_SESSION = "<session cookie value of a signed-in clinician>"
$env:MEDIBRIDGE_MCP_RUN_ID  = "<workflow run id>"
uv run python -m medibridge_mcp
```

The server binds to one workflow run, and the patient always comes from that run. Every call re-validates the session and goes through the same tool-layer authorization as the agents. Write tools are never exposed.

## Tests and evaluation

```powershell
uv run pytest tests -q                         # 191 backend, security, workflow and agent tests (uses database medibridge_test;
                                               # A2A calls go to the real agent apps in-process, no ports needed)
uv run pytest tests -q -m "not slow"          # skip tests that run a local model
cd frontend; npm run typecheck; npm run build
npx playwright test                            # e2e against the running stack (desktop + phone viewport)
uv run python -m tests.ai_eval.asr_eval        # Whisper word error rate on synthetic audio
uv run python -m tests.ai_eval.ner_eval        # extraction accuracy vs hand-labelled gold (about 2 min per case on CPU)
uv run python -m tests.ai_eval.retrieval_eval  # hit@k / MRR and the cross-patient leakage check
uv run python -m tests.ai_eval.acceptance_live --scenario af_warfarin_ibuprofen   # full live scenario through the API
```

Evaluation reports are written to `var/eval/`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Health shows Ollama missing models | `ollama pull qwen3.5:9b qwen3.5:4b bge-m3` |
| Extraction or handoff very slow, or a run fails with `provider_unavailable` | Not enough free RAM: close other model servers. Check what is loaded with `ollama ps` |
| "Document search unavailable" in a handoff | Qdrant is down; `docker compose -p medibridge up -d`. The handoff states that reports were not reviewed |
| "Medication screening NOT performed" | Neo4j is down or no knowledge release is loaded; run `medibridge_knowledge.load` |
| A run shows `interrupted` | The API restarted mid-run. Resume it from Workflow & approvals |
| Health shows "Agent services (A2A)" failing, or a run fails with `agent_unreachable` / `agent_timeout` | An agent service is not running or did not answer. Check `var/logs/agent-<id>.err.log`, then `scripts\stop.ps1 -KeepServices` and `scripts\start.ps1`. Documents and medication safety degrade instead of failing (the handoff says what was not checked) |
| A run is blocked with `a2a_capability_rejected` or `a2a_capability_state_mismatch` | An agent refused the supervisor's token: usually the API and the agents were started with different `.env` values (restart everything), or the clocks differ. Refusals are in the audit log |
| API fails to start: `MEDIBRIDGE_A2A_SECRET must be at least 32 characters` | `.env` predates A2A. Run `scripts\setup.ps1` once (it appends the A2A settings with a new secret) |
| Upload rejected with 413 | Over 50 MB for audio or documents (1 MB for other requests) |
