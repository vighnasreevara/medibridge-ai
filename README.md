# MediBridge AI

> **New machine?** See [SETUP.md](SETUP.md) for end-to-end setup steps (written so Claude Code can follow them).

> **Synthetic-data research and demo platform. Not approved for clinical use.**
> AI supports healthcare professionals; it does not replace clinical judgment.

MediBridge AI is a local-first **cardiology** intake and doctor-handoff system built from cooperating AI agents:

1. The nurse finds the patient in the **Patient Registry** (MRN and date of birth), or registers a first-time patient.
2. The intake conversation is recorded; the **Ambient Scribe** transcribes it locally and, after a person reviews the transcript, extracts symptoms, medicines, allergies and conditions.
3. The **Chart Agent** reads the structured record (PostgreSQL), **Evidence Retrieval** finds the relevant prior reports (BM25 + vector search, fused and re-ranked), and **Medication Safety** screens current medicines and doses against a cardiology drug-knowledge graph (Neo4j, FDA/NLM sources).
4. The **Clinical Handoff Brief** writes a structured, cited cardiology brief (SBAR) after deterministic pre-generation checks. A doctor reviews and approves it.
5. The doctor writes the assessment, plan and orders. **Order Entry** has **Medication Safety** screen the proposed orders agent-to-agent. Contraindications go back to the doctor once: override with a reason, or revise.
6. The **Chart Agent** writes the record in one audited transaction, and the **After-Visit Summary** agent writes the patient's own plain-language summary of the visit, which the doctor releases.

A **Care Orchestrator** (LangGraph) runs the workflow and four human gates; the eight specialist agents are separate local services it calls over the **A2A** protocol. Nothing reaches the record without a doctor's approval, and no medical data leaves the machine.

## Quick start (Windows)

```powershell
& .\scripts\setup.ps1    # once: secrets, dependencies, local models, data services, migrations, Synthea cardiac cohort,
                         #       drug knowledge graph, document index, re-ranker, demo audio
& .\scripts\start.ps1    # data services + 8 agent services (127.0.0.1:8101-8108)
                         #       + API (127.0.0.1:8000) + web (127.0.0.1:5173)
```

Open http://127.0.0.1:5173. Demo accounts (cardiologists, cardiac nurses, administrator) are generated with random passwords in `var/demo-credentials.txt`. See [docs/runbook.md](docs/runbook.md) for the demo walkthrough, operations and troubleshooting.

## The agents

| Agent (A2A service) | Port | What it does | Skills |
|---|---|---|---|
| **Care Orchestrator** (supervisor, in the API) | - | Plans the run, enforces dependencies and the four gates, routes failures, finalizes | - |
| **Patient Registry (EMPI)** | 8101 | Master patient index: matches a returning patient by MRN + date of birth (or name + date of birth), registers a new patient with a duplicate check; confirms identity and consent before intake | `match_or_register_patient`, `verify_encounter_identity` |
| **Ambient Scribe** | 8102 | Local speech recognition (faster-whisper) with misheard-drug-name flags; extraction of findings from the reviewed transcript with verbatim grounding and negation checks | `transcribe_intake_audio`, `extract_clinical_entities` |
| **Chart Agent (EHR)** | 8103 | Reads the structured record (conditions, medicines with doses, allergies, lab results and vitals with trends, procedures, visits); writes the approved encounter back in one transaction with its audit entries | `assemble_patient_chart`, `commit_encounter_record` |
| **Evidence Retrieval** | 8104 | Hybrid search over the patient's reports: BM25 with medical tokenisation + dense vectors (Qdrant), reciprocal rank fusion, cross-encoder re-ranking; every hit re-validated in Postgres | `retrieve_clinical_evidence` |
| **Medication Safety CDS** | 8105 | Deterministic screen against the cardiology knowledge graph: interactions (label dose limits checked against the recorded dose), allergies, drug-condition cautions | `screen_current_medications`, `screen_proposed_orders` (peer-only) |
| **Clinical Handoff Brief** | 8106 | Structured SBAR cardiology brief; pre-generation checks; every statement cited and validated; privacy filter | `generate_handoff_brief` |
| **Order Entry (CPOE)** | 8107 | Drafts the note; verifies the doctor's signed orders and asks Medication Safety to screen them (agent-to-agent, delegated) | `draft_clinical_note`, `verify_proposed_orders` |
| **After-Visit Summary** | 8108 | The patient's summary of this visit: reason in their words, medicines and how to take them, next steps, when to get help | `generate_after_visit_summary` |

## Data (all synthetic, open source)

- **Patients:** a [Synthea](https://github.com/synthetichealth/synthea) 4.0.0 cardiac cohort (Apache-2.0), generated locally with a keep module that retains only patients with atrial fibrillation, heart failure, coronary disease or myocardial infarction. Only the cardiology-relevant part of each record is imported: cardiac diagnoses and risk factors, cardiovascular medicines with dose and frequency, lipids/renal/INR/troponin/NT-proBNP/HbA1c/ejection fraction, vitals, cardiac procedures, drug allergies. Reports (PDF) are rendered from those values only.
- **Drug knowledge:** 65 cardiology ingredients (anticoagulants, antiplatelets, beta-blockers, ACE inhibitors/ARBs/ARNI, calcium-channel blockers, diuretics/MRAs, statins, antiarrhythmics, nitrates, SGLT2 inhibitors and their interaction partners) from NLM RxNorm, RxClass MED-RT and FDA EPC, plus openFDA labels. 66 curated interactions and 2 drug-condition cautions, each quoting its FDA label verbatim (the build refuses any quote it cannot find), batch-loaded into Neo4j as a versioned release.

## What you can do

| Region | What you can do |
|---|---|
| Overview | Today's cardiology clinic, items awaiting review, live workflow activity, component health |
| Patients | Registry search; clinical record: timeline, results with trend charts, conditions, medications (current / historical / unknown), allergies, notes, documents, vitals |
| Intake | Patient Registry (find or register), identity verification, consent, audio recording or upload, transcript review |
| Encounters | Workspace with findings and provenance, escalation flags, conflicts, prior visits, results |
| Handoff briefs | SBAR brief with evidence citations and pre-generation checks; doctor edits as new versions; hash-bound approval; note and orders; medication-safety review (override with reason, or revise); the patient's after-visit summary (printable, released by the doctor) |
| Medical records | Document library, redacted text viewer, audited originals, hybrid search within one patient's reports, uploads |
| Medication & safety | Recorded medicines resolved to RxNorm, evidence-cited alerts with label quotes and dose checks, completeness statement, drug-relationship graph |
| Agent mission control | Live workflow graph from real execution records; per-step A2A calls, peer calls, tool calls, models, tokens; live status of the eight agent services |
| Workflow & approvals | Gates waiting for a person (transcript, brief, orders, safety review), failed or interrupted runs, recent decisions |
| Observability | Agent, A2A call, tool, model and retrieval latency, tokens, guardrail outcomes |
| Audit & governance | Hash-chained audit log with live verification (administrators) |
| Administration | Users and roles, care-team access, local model selection, configuration, diagnostics |

## Architecture

Everything runs on one machine. Every port binds to `127.0.0.1`, and no medical data is sent to a hosted service.

### 1. System architecture

```mermaid
flowchart TB
  subgraph Users["People (browser)"]
    N["Cardiac nurse"]
    D["Cardiologist"]
    A["Administrator"]
  end

  subgraph Web["Web app - React 19 + TypeScript + Vite :5173"]
    UI["12 regions incl. Patient Registry, SBAR briefs,<br/>orders and safety review, after-visit summary,<br/>results trends, Mission control"]
    LIVE["SSE live client (events only invalidate queries)"]
  end

  subgraph API["FastAPI :8000"]
    MW["Boundary: Host allow-list, CSRF header, Origin,<br/>body limits, security headers, no access log"]
    AUTH["Argon2id sessions + RBAC (3 roles, 27 permissions)<br/>+ care-team patient scope"]
    ROUTES["Routes: registry, patients, encounters, intake, workflow,<br/>handoff, orders, after-visit summary, documents, knowledge,<br/>events, agents, observability, audit, admin"]
    AUD["Audit writer (append-only, hash-chained)"]
  end

  subgraph ORCH["Care Orchestrator - LangGraph (in the API process)"]
    GRAPH["17-step state machine, 4 human gates,<br/>Postgres checkpoints, A2A proxies"]
    A2AC["A2A client: agent cards, per-call capability tokens,<br/>delegations for peer calls, key allow-lists"]
  end

  subgraph SVC["Agent services - 8 processes, A2A JSON-RPC on 127.0.0.1:8101-8108"]
    REG["Patient Registry :8101"]
    SCR["Ambient Scribe :8102"]
    CHART["Chart Agent :8103"]
    EVR["Evidence Retrieval :8104"]
    MED["Medication Safety :8105"]
    HB["Handoff Brief :8106"]
    OE["Order Entry :8107"]
    AVS["After-Visit Summary :8108"]
    TOOLS["Tool layer (15 tools): read/write split,<br/>caller allow-lists, id checks"]
  end

  subgraph AI["Local AI"]
    OLL["Ollama :11434 - qwen3.5:9b / 4b, bge-m3"]
    ASR["faster-whisper small.en (CPU)"]
    RR["Cross-encoder re-ranker (ONNX, CPU)"]
  end

  subgraph DATA["Data services (Docker, 127.0.0.1 only)"]
    PG[("PostgreSQL 17<br/>clinical record, observations, workflow,<br/>audit, checkpoints")]
    QD[("Qdrant 1.19<br/>dense vectors, patient-filtered")]
    NEO[("Neo4j 5.26<br/>cardiology drug knowledge release")]
  end

  SRC["Build time only: Synthea 4.0.0 (Java), NLM RxNav, openFDA labels"]

  N & D & A --> UI
  UI <--> MW --> AUTH --> ROUTES
  LIVE <--> ROUTES
  ROUTES --> AUD --> PG
  ROUTES -->|"start / resume / gate decisions"| GRAPH --> A2AC
  ROUTES -->|"registry calls (actor-only capability)"| A2AC
  A2AC -->|"SendMessage + Bearer capability"| REG & SCR & CHART & EVR & MED & HB & OE & AVS
  OE -.->|"peer call (delegated capability)"| MED
  REG & SCR & CHART & EVR & MED & HB & OE & AVS --> TOOLS
  TOOLS --> PG & QD & NEO
  SCR --> ASR
  SCR & HB & OE & AVS & EVR --> OLL
  EVR --> RR
  GRAPH -->|"checkpoints"| PG
  SRC -.-> PG & NEO
```

### 2. End-to-end workflow

```mermaid
sequenceDiagram
  autonumber
  actor Nurse
  actor Doctor
  participant API as FastAPI
  participant OR as Care Orchestrator
  participant AG as Agent services (A2A)
  participant PG as PostgreSQL
  participant KG as Neo4j
  participant QD as Qdrant
  participant LLM as Ollama (local)

  Nurse->>API: Find patient (MRN + date of birth) or register a new one
  API->>AG: A2A Patient Registry (actor-only capability)
  AG->>PG: match, or duplicate check + create record + care team, audit
  Nurse->>API: Create encounter, verify identity, record consent, upload audio
  API->>OR: start run
  OR->>AG: Patient Registry: identity + consent checked
  OR->>AG: Ambient Scribe: transcribe (local Whisper), flag misheard drug names
  OR-->>Nurse: GATE 1 transcript review
  Nurse->>API: confirm reviewed transcript
  OR->>AG: Ambient Scribe: extract findings (schema-constrained, validated)
  AG->>LLM: structured extraction
  OR->>AG: Chart Agent: record, results with trends, conflicts
  AG->>PG: read through the tool layer
  OR->>AG: Evidence Retrieval: BM25 + dense, RRF, re-rank
  AG->>QD: dense search (this patient only)
  AG->>PG: BM25 corpus + re-validation of every hit
  OR->>AG: Medication Safety: current medicines with doses
  AG->>KG: interactions, dose limits, allergies, conditions
  OR->>AG: Handoff Brief: pre-generation checks, SBAR brief
  AG->>LLM: cited statements, validated one by one
  OR-->>Doctor: GATE 2 brief review (approve / request changes / reject)
  Doctor->>API: approve exact version (hash)
  OR->>AG: Order Entry: note draft
  OR-->>Doctor: GATE 3 note and orders
  Doctor->>API: prepare, then sign the exact change set
  OR->>AG: Order Entry: verify signed orders
  AG->>AG: peer call to Medication Safety (delegated capability)
  alt blocking alert
    OR-->>Doctor: GATE 4 safety review
    alt override with reason
      Doctor->>API: acknowledge every alert + reason (approval record)
    else revise
      Doctor->>API: revise, back to GATE 3, orders screened again
    end
  end
  OR->>AG: Chart Agent: ONE transaction - note, findings, orders, approvals, finalized encounter, audit
  OR->>AG: After-Visit Summary: patient's summary of this visit
  Doctor->>API: release the summary to the patient (hash-bound)
```

### 3. State machine (LangGraph)

```mermaid
stateDiagram-v2
  state "GATE 1: transcript review" as transcript_review
  state "GATE 2: brief review (doctor)" as handoff_review
  state "GATE 3: note and orders (doctor)" as orders_review
  state "GATE 4: safety review (doctor)" as safety_review
  [*] --> supervisor_start
  supervisor_start --> registry_check
  registry_check --> scribe_transcribe: identity + consent
  scribe_transcribe --> transcript_review
  transcript_review --> scribe_extract: confirmed
  scribe_extract --> chart_review
  chart_review --> evidence_retrieval
  evidence_retrieval --> medication_screen: evidence / none / unavailable (degrades)
  medication_screen --> handoff_brief: screened / unavailable (degrades)
  handoff_brief --> handoff_review
  handoff_review --> order_draft: approve
  handoff_review --> handoff_brief: request changes
  handoff_review --> finalize: reject
  order_draft --> orders_review
  orders_review --> order_safety_check: signed
  orders_review --> finalize: cancel
  order_safety_check --> chart_commit: no blocking alert
  order_safety_check --> safety_review: blocking alert
  safety_review --> chart_commit: override with reason
  safety_review --> orders_review: revise
  chart_commit --> after_visit_summary
  after_visit_summary --> finalize
  finalize --> [*]

  note right of finalize
    Critical failures route to finalize.
    A restart marks the run interrupted;
    it resumes from the last checkpoint.
  end note
```

### 4. Agents, peer calls and data access

```mermaid
flowchart LR
  OR["Care Orchestrator"]
  subgraph Agents["Agent services (A2A)"]
    REG["Patient Registry"]
    SCR["Ambient Scribe"]
    CH["Chart Agent"]
    EV["Evidence Retrieval"]
    MS["Medication Safety"]
    HB["Handoff Brief"]
    OE["Order Entry"]
    AV["After-Visit Summary"]
  end
  OR -->|"capability per call"| Agents
  OE -.->|"delegated peer call:<br/>screen_proposed_orders"| MS

  subgraph Read["Read tools"]
    T1["patient.get_history / encounter.get"]
    T2["document.search (hybrid)"]
    T3["graph.screen_patient / graph.lookup_drug"]
  end
  subgraph Write["Write tools (never model-callable)"]
    W1["handoff.create_draft"]
    W2["documentation.prepare_update (doctor) /<br/>documentation.commit_approved (Chart Agent)"]
  end
  CH --> T1 & W2
  EV --> T2
  MS --> T3
  HB --> W1
  T1 & W1 & W2 --> PG[("PostgreSQL")]
  T2 --> QD[("Qdrant")] & PG
  T3 --> NEO[("Neo4j")]
  REG --> PG
  classDef w fill:#f4ebfb,stroke:#7a2fb8
  class W1,W2 w
```

### 5. Security and guardrail layers

```mermaid
flowchart TB
  R["HTTP request"] --> L1["1. Boundary: loopback host, CSRF header, Origin, body size, headers"]
  L1 --> L2["2. Session: hashed token, idle 30 min, absolute 8 h"]
  L2 --> L3["3. RBAC permission (deny by default)"]
  L3 --> L4["4. Patient scope: care-team assignment (404 + audit otherwise)"]
  L4 --> L5["5. EMPI: MRN must match date of birth; duplicates shown before registering"]
  L5 --> L6["6. AI guardrails: pre-generation checks, untrusted-data framing,<br/>verbatim-quote extraction, cited + number-checked brief, read-only model tools"]
  L6 --> LA["7. A2A boundary: per-call HMAC capability bound to run, patient, actor, agent,<br/>skill (single use); delegated peer calls; input/output key allow-lists"]
  LA --> L7["8. Human gates: approvals bound to content SHA-256;<br/>orders screened before commit; overrides need a reason"]
  L7 --> L8["9. Database triggers: doctor-only prescribing/approval, append-only versions"]
  L8 --> L9["10. Hash-chained append-only audit log"]
```

### 6. Data pipeline: cohort, knowledge, documents and hybrid retrieval

```mermaid
flowchart LR
  subgraph Cohort["Synthea cardiology cohort"]
    SY["Synthea 4.0.0 (Java)<br/>keep module: cardiac diagnoses"] --> FH["FHIR R4 bundles"]
    FH --> FI["Import filter: cardiac conditions,<br/>cardiovascular medicines (dose, frequency),<br/>cardiac labs + vitals, cardiac procedures"]
    FI --> PGc[("PostgreSQL")]
    FI --> PDF["Reports rendered from imported values"]
  end
  subgraph Knowledge["Cardiology drug knowledge"]
    RX["RxNorm / MED-RT / FDA EPC<br/>openFDA labels (65 ingredients)"] --> VER{"Every curated quote found<br/>verbatim in the label?"}
    CU["66 interactions (some with dose limits),<br/>2 condition cautions, 3 allergy maps"] --> VER
    VER -->|"yes"| BATCH["Batched UNWIND load -> new release -> atomic switch"]
    BATCH --> NEO[("Neo4j")]
  end
  subgraph Retrieval["Hybrid retrieval (per query, one patient)"]
    Q["Question"] --> BM["BM25 over Postgres chunks<br/>(medical tokenisation)"]
    Q --> DE["bge-m3 dense search in Qdrant"]
    BM & DE --> RRF["Reciprocal rank fusion"]
    RRF --> RE["Cross-encoder re-rank (third vote)"]
    RE --> VAL["Re-validate in Postgres, redact identifiers"]
  end
  PDF --> ING["Extract, chunk, embed"] --> QDi[("Qdrant")]
```

### 7. Data model (main tables)

```mermaid
erDiagram
  PATIENTS ||--o{ ENCOUNTERS : has
  PATIENTS ||--o{ CARE_TEAM_ASSIGNMENTS : "scoped by"
  PATIENTS ||--o{ CONDITIONS : has
  PATIENTS ||--o{ MEDICATION_STATEMENTS : "dose, frequency"
  PATIENTS ||--o{ ALLERGIES : has
  PATIENTS ||--o{ OBSERVATIONS : "LOINC labs + vitals"
  PATIENTS ||--o{ PROCEDURES : has
  ENCOUNTERS ||--o{ TRANSCRIPTION_RECORDS : "raw + reviewed"
  ENCOUNTERS ||--o{ CLINICAL_NOTES : has
  ENCOUNTERS ||--o{ PRESCRIPTIONS : "doctor only"
  ENCOUNTERS ||--o{ HANDOFF_REPORTS : has
  HANDOFF_REPORTS ||--o{ HANDOFF_VERSIONS : "append-only, SHA-256"
  ENCOUNTERS ||--o{ DOCUMENTATION_CHANGESETS : "signed, screened, committed"
  ENCOUNTERS ||--o{ AFTER_VISIT_SUMMARIES : "draft -> released"
  APPROVAL_RECORDS }o--|| ENCOUNTERS : "brief, orders, safety override, summary"
  ENCOUNTERS ||--o{ WORKFLOW_RUNS : runs
  WORKFLOW_RUNS ||--o{ AGENT_EXECUTIONS : "agent, a2a, tool (no content)"
  WORKFLOW_RUNS ||--o{ WORKFLOW_EVENTS : "SSE source"
  PATIENTS ||--o{ MEDICAL_DOCUMENTS : owns
  MEDICAL_DOCUMENTS ||--o{ DOCUMENT_CHUNKS : "BM25 corpus + vectors"
```

Each clinical fact carries its provenance: `source_kind` (patient-reported, clinician-documented, record-retrieved, unconfirmed), `source_ref` (the Synthea resource, transcript segment, note or document chunk), `source_phrase`, `certainty` and `status`. Facts a demo scenario needed but the generated record lacked are marked `scenario-augmentation:<key>`.

### 8. Live monitoring

```mermaid
flowchart LR
  CALL["Orchestrator A2A call"] -->|"kind a2a"| EX[("agent_executions")]
  CALL -->|"a2a.request / a2a.response"| EV[("workflow_events")]
  NODE["Agent node / peer call / tool call"] -->|"parent = the A2A call"| EX
  NODE --> EV
  EV --> SSE["GET /api/events/stream"] --> CL["Browser live client"] --> Q["Query re-fetch"]
  Q --> MC["Mission control, intake progress, approvals, orders panel"]
  EX --> OBS["Observability summary"]
```

### 9. One A2A call, with a delegated peer call

```mermaid
sequenceDiagram
  autonumber
  participant OR as Orchestrator step
  participant CL as A2A client
  participant OE as Order Entry service
  participant MS as Medication Safety service
  participant PG as PostgreSQL

  OR->>PG: a2a execution started
  OR->>CL: skill verify_proposed_orders + its input keys only
  CL->>CL: mint capability (Order Entry) + delegated capability (Medication Safety, via Order Entry)
  CL->>OE: SendMessage, Bearer capability, delegations
  OE->>OE: verify signature, audience, expiry, nonce, state ids = token ids, allowed inputs, allowed delegations
  OE->>MS: SendMessage screen_proposed_orders, Bearer delegated capability
  MS->>MS: verify, and accept only if via = Order Entry
  MS->>PG: current medicines + doses, allergies, conditions, then screen in Neo4j
  MS-->>OE: order_screen (allowed output key only)
  OE->>PG: attach the screen to the exact signed change set
  OE-->>CL: state delta (documentation)
  CL->>CL: reject any key outside the skill contract
  CL-->>OR: delta, or unavailable, or refused
  OR->>PG: a2a execution finished
```

Refusals (bad or reused tokens, another patient's state, over-sharing, a peer-only skill called directly, a rogue response) are audited and block the run. Set `AGENT_TRANSPORT=inprocess` to run the same agent code inside the API for debugging.

**Limitation.** The services share one `.env`, so they share the token secret and the database credentials. A token proves it was issued by a MediBridge process on this machine, not specifically by the orchestrator; access is still bounded per call by the token's binding and by the tool layer's permission checks inside each agent.

- **Design and discovery report:** [docs/architecture/00-discovery-and-architecture.md](docs/architecture/00-discovery-and-architecture.md)
- **Guardrail enforcement and tests:** [docs/guardrails.md](docs/guardrails.md)
- **Evaluation results:** [docs/evaluation.md](docs/evaluation.md)
- **Implementation status:** [docs/IMPLEMENTATION-REPORT.md](docs/IMPLEMENTATION-REPORT.md)

## Repository layout

```
backend/medibridge_api      FastAPI app: models, routes (incl. registry), security, guardrails, providers,
                            services (hybrid retrieval: lexical.py BM25, rerank.py, vector_index.py)
agents/medibridge_agents    Care Orchestrator graph, agent nodes (registry, scribe, chart, retrieval, safety, brief,
                            orders, after-visit summary), tool layer, escalation rules;
                            a2a/: services, client, capabilities, peer calls, registry of agents and skills
mcp/medibridge_mcp          MCP server (read-only tools)
knowledge/                  Cardiology drug-knowledge fetcher, curated label-quoted set, batched Neo4j loader, screen
database/                   Alembic migrations; seed: Synthea generator + cardiac importer + scenarios
frontend/                   Dashboard (12 regions), Vitest + Playwright tests
infrastructure/             Docker Compose (Postgres, Qdrant, Neo4j; 127.0.0.1 only)
tests/                      Backend, security, workflow tests and AI evaluation harnesses
scripts/                    setup, start, stop, synthetic audio
docs/                       Architecture, guardrails, evaluation, runbook, implementation report
```

## Reuse and licences

- **Synthea** (Apache-2.0): generates the synthetic cohort locally; no Synthea source is vendored (the release jar is downloaded and checksum-verified).
- **UI and live-event patterns:** common mission-control dashboard patterns (status roles, SSE invalidation), re-implemented from scratch.
- **Password hashing and sessions:** standard Argon2id + server-side session design, written for this project (`backend/medibridge_api/security/passwords.py`).
- **Drug reference data:** RxNorm (NLM), MED-RT (VA/NLM) and FDA Established Pharmacologic Class are US Government public data; openFDA labels are CC0. The curated interaction set is demo-curated and **not clinically reviewed**.
- **Models:** Qwen3.5 (Apache-2.0), bge-m3 (MIT), Whisper small.en (MIT), jina-reranker-v1-tiny-en (Apache-2.0), Ollama (MIT).

## Author

Created and maintained by [vighnasreevara](https://github.com/vighnasreevara).
