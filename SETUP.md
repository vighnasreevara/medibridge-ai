# MediBridge AI — end-to-end setup on a new machine

> **For Claude Code:** this file is your runbook. Work through the steps in order, run each command from the
> project root (the folder that contains this file), and check the "Verify" line before moving on. If a step
> fails, read the log named in that step, fix the cause, and rerun only that step. `scripts\setup.ps1` is safe to
> rerun: it never overwrites an existing `.env` and skips work already done. Every service must stay bound to
> `127.0.0.1`. Ask the user before installing anything with `winget`, and never commit or print `.env`.
>
> **For a person:** open Claude Code in this folder and say: *"Follow SETUP.md and set up this project end to end."*

MediBridge AI is a local-first, synthetic-data cardiology intake and doctor-handoff demo:

- a FastAPI API (`:8000`) with a LangGraph orchestrator;
- eight A2A agent services (`:8101`–`8108`);
- a React/Vite web app (`:5173`);
- PostgreSQL, Qdrant and Neo4j in Docker;
- local AI through Ollama (`:11434`), faster-whisper and an optional ONNX re-ranker.

**Not for clinical use.**

---

## 0. What the zip does and doesn't contain

Included: all source, `pyproject.toml` + `uv.lock`, `frontend/package-lock.json`, Alembic migrations, the Synthea
keep module, curated drug-knowledge files and manifests, tests and docs.

**Not included** (setup regenerates all of it):

| Missing | Recreated by |
|---|---|
| `.env` (secrets) | Setup, from `.env.example`, with new random secrets |
| `.venv/`, `frontend/node_modules/` | `uv sync`, `npm ci` |
| `var/` (Synthea cohort, PDFs, audio, models cache, logs, demo passwords) | Setup |
| `knowledge/sources/raw/` (drug label downloads) | `medibridge_knowledge.fetch` |
| Docker volumes, Ollama models | Setup |

## 1. Prerequisites

| Requirement | Version | Check | Install (ask the user first) |
|---|---|---|---|
| Windows 11 + PowerShell 5.1+ | — | `$PSVersionTable.PSVersion` | — |
| Docker Desktop (WSL2 backend) | 4.36+ | `docker version` | `winget install --id Docker.DockerDesktop --exact` (then start Docker Desktop once and accept the terms) |
| uv | 0.11+ | `uv --version` | `winget install --id astral-sh.uv --exact` |
| Python | 3.12 or 3.13 | `uv python list` | Installed by uv automatically on `uv sync` |
| Node.js + npm | 22.12+ (tested 22.14) | `node --version` | `winget install --id OpenJS.NodeJS.LTS --exact` |
| Ollama (system install) | 0.40.0 | `& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" --version` | `winget install --id Ollama.Ollama --version 0.40.0 --exact` |
| Java | 17+ (first setup only, for Synthea) | `java -version` | `winget install --id Microsoft.OpenJDK.21 --exact` |
| Windows voices Zira + David | built in on en-US Windows | — | Used to synthesize demo audio |
| RAM / disk | 32 GB recommended (16 GB works slowly) / ~20 GB free | — | — |

`setup.ps1` expects Ollama at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`, which is where the winget install
puts it. Ollama must be serving on `127.0.0.1:11434`. The tray app does this after install. Otherwise run
`ollama serve`.

After installing tools, **open a new terminal** so PATH updates apply.

**Verify:** `docker info` succeeds (the engine is running), and `uv`, `node`, `npm` and `java` all resolve.

## 2. Free the ports

MediBridge uses `5432` (Postgres), `6333/6334` (Qdrant), `7474/7687` (Neo4j), `8000`, `8101–8108`, `5173`
and `11434`. List anything that already holds them:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 5432,6333,6334,7474,7687,8000,5173,11434,8101,8102,8103,8104,8105,8106,8107,8108 |
  Select-Object LocalPort, OwningProcess, @{n='Process';e={(Get-Process -Id $_.OwningProcess).ProcessName}}
docker ps --format "{{.Names}}  {{.Ports}}"
```

Stop conflicting containers with `docker stop <name>`. Port 11434 should be held only by Ollama.

## 3. One-command setup

```powershell
& .\scripts\setup.ps1
```

If PowerShell blocks the script, run it for this process only:
`powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1`. Don't change the machine policy.

It runs these steps in order. If a step fails, you can run it by hand:

| # | Step | Manual command |
|---|---|---|
| 1 | `.env` from `.env.example`, with every `CHANGE_ME` replaced by a random secret | — |
| 2 | Python dependencies | `uv sync` |
| 3 | Frontend dependencies | `cd frontend; npm ci; cd ..` |
| 4 | Ollama models (~9 GB) | `ollama pull qwen3.5:9b`; `ollama pull qwen3.5:4b`; `ollama pull bge-m3` |
| 5 | Data services (Postgres 17, Qdrant 1.19, Neo4j 5.26) | `& .\scripts\start.ps1 -ServicesOnly` |
| 6 | Database migrations | `uv run alembic -c database\alembic.ini upgrade head` |
| 7 | Synthea 4.0.0 cardiac cohort (downloads the jar once, checksum-verified; needs Java) | `uv run python -m medibridge_seed.synthea generate` |
| 8 | Seed the cardiology dataset + demo accounts | `uv run python -m medibridge_seed` |
| 9 | Drug knowledge (NLM RxNav + openFDA, needs internet) → Neo4j | `uv run python -m medibridge_knowledge.fetch` then `uv run python -m medibridge_knowledge.load` |
| 10 | Optional re-ranker (only if `RERANKER_MODEL` is set in `.env`) | `uv run python -m medibridge_api.services.rerank --download` |
| 11 | Embed and index the reports into Qdrant | `uv run python -m medibridge_api.cli ingest` |
| 12 | Synthetic intake audio | `& .\scripts\make_synthetic_audio.ps1` |

Expect 20–40 minutes on first run, mostly model downloads.

**Verify:**
- `var\demo-credentials.txt` exists. It holds the demo nurse, doctor and admin logins, with passwords randomized at every seed.
- `docker compose -p medibridge ps` shows three healthy containers.
- `var\fixtures\audio\` contains `.wav` files.

## 4. Start the app

```powershell
& .\scripts\start.ps1
```

This starts the data services, the 8 agent services (waiting for each agent card), the API on `127.0.0.1:8000` and
the web dev server on `127.0.0.1:5173`, then prints health. Open **http://127.0.0.1:5173** and sign in with an
account from `var\demo-credentials.txt`.

Stop everything with `& .\scripts\stop.ps1`. Add `-KeepServices` to leave the Docker data services running.

**Verify:**
- `Invoke-RestMethod http://127.0.0.1:8000/api/health` reports every component OK.
- `uv run python -m medibridge_agents.a2a --list` lists 8 agents.
- Agent logs are in `var\logs\agent-<id>.err.log`.

## 5. Smoke test (demo walkthrough, ~15 min)

1. Sign in as the **nurse**. Go to **Intake**, find a patient by MRN + date of birth (see `var\seed-manifest.json`), verify identity and record consent.
2. Upload `var\fixtures\audio\af_warfarin_ibuprofen.wav`, review the transcript and confirm it.
3. Watch **Agent mission control** while extraction, chart, retrieval, medication screen and brief run (5–10 min on CPU).
4. Sign in as the **doctor**. Approve the brief in **Handoff briefs**, add an order (for example aspirin 81 mg), sign, and override or revise the safety alert.
5. Release the after-visit summary. As **admin**, check that the audit chain verifies in **Audit & governance**.

More detail is in `docs/runbook.md`.

## 6. Tests

```powershell
uv run pytest tests -q -m "not slow"     # fast backend/security/workflow tests (needs the data services running)
uv run pytest tests -q                   # full suite, including tests that call the local model
cd frontend; npm run typecheck; npm run build; npx playwright test; cd ..
```

## 7. Configuration notes

- All settings live in `.env` (documented inline in `.env.example`). Defaults are correct for a local install.
- On a smaller machine, set `LLM_MODEL_CLINICAL=qwen3.5:4b` in `.env` (faster, slightly less accurate).
- `AGENT_TRANSPORT=inprocess` runs all agents inside the API, which helps with debugging port problems.
- Hosted LLMs are refused unless `ALLOW_HOSTED_PHI=true` **and** `HOSTED_PHI_APPROVAL_REF` are set. Leave them off.
- LangSmith tracing is off by default.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Ollama is not installed at ...` | Install with winget (step 1), or reinstall to the default location |
| `Port N is held by container 'X'` | `docker stop X`, then rerun |
| `docker compose up failed` | Start Docker Desktop and wait for "Engine running". Then `docker compose -p medibridge logs` |
| `Java 17+ is required` | Install OpenJDK 21, open a new terminal, rerun `setup.ps1` |
| Knowledge fetch fails (offline/firewall) | Setup retries with `--offline` using cached files. On a fresh machine it needs internet once |
| Agent `X` did not start | Read `var\logs\agent-X.err.log`. Usually a port conflict or a missing `.env` key; rerun `setup.ps1`, which appends missing A2A settings |
| Very slow, or `provider_unavailable` | Not enough free RAM. Close other model servers, check `ollama ps`, or switch to the 4b model |
| Lost demo passwords | `uv run python -m medibridge_seed --reset` then `uv run python -m medibridge_api.cli ingest` (**destroys local demo data**) |
| No Zira/David voice | Install the English (United States) speech pack in Windows Settings → Time & language → Speech, then rerun `make_synthetic_audio.ps1` |
