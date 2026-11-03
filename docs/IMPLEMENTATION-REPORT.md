# MediBridge AI — implementation report

> **Status: synthetic-data research and demo platform. Not approved for clinical use.**
> Built 2026-10-20 on a single Windows 11 laptop (Intel Core Ultra 9 185H, 32 GB RAM, no discrete GPU).

## Phase 2 (cardiology) at a glance

Requested by the owner after the A2A release: cardiology-only data from open sources, industry-standard agent roles,
a patient registry, hybrid retrieval with ranking, a structured cardiology brief with pre-generation guardrails, an
order-safety loop with a single doctor override, a patient-facing after-visit summary, and a better UI.

| Request | Status | Where |
|---|---|---|
| Patient identity = find a returning patient or register a new one | **Done** | Patient Registry (EMPI) agent; `/api/registry/match`, `/api/registry/register`; Intake page panel |
| Merge speech processing and clinical NER | **Done** | Ambient Scribe agent (two skills, transcript gate between them) |
| Chart from Postgres; updates and logging after approval | **Done** | Chart Agent: `assemble_patient_chart` (now with lab and vital trends) and `commit_encounter_record` (one transaction + audit) |
| Documents: BM25 + Qdrant with ranking (RRF) | **Done** | Evidence Retrieval: exact BM25 with medical tokenisation + dense, RRF, cross-encoder re-rank as a third vote; measured per variant |
| Medication safety from trusted sources, batch-loaded into Neo4j, using current drugs and doses | **Done** | 65 cardiology ingredients (NLM, FDA); 66 label-quoted interactions incl. dose limits; batched UNWIND load; doses from Postgres |
| Structured doctor brief with guardrails before generation | **Done** | SBAR cardiology brief (17 sections in 5 groups); deterministic pre-generation checks |
| Doctor's proposed drugs checked by medication safety; failure goes back to the doctor once; override goes straight to final processing | **Done** | Orders loop: Order Entry -> Medication Safety (A2A peer call) -> safety review gate -> override (reason, approval record) or revise (re-screened) |
| Records and logging updated after approval | **Done** | Chart commit + audit entries + approval records; outbox indexes the note |
| Patient report: this visit, issue, drugs, dose, when to take them | **Done** | After-Visit Summary agent; doctor releases it; printable page |
| Industry-standard, catchy agent names | **Done** | Care Orchestrator, Patient Registry (EMPI), Ambient Scribe, Chart Agent (EHR), Evidence Retrieval, Medication Safety CDS, Clinical Handoff Brief, Order Entry (CPOE), After-Visit Summary |
| Cardiac data only, downloaded open-source data | **Done** | Synthea 4.0.0 cardiac cohort (checksum-verified download, local generation); cardiac-only import filter |
| A2A between the agents | **Done** | Orchestrator -> 8 services; Order Entry -> Medication Safety peer call with a delegated capability |
| Improve the UI | **Done** | Registry panel, SBAR brief layout, orders/safety review panel, after-visit summary + print page, results trend charts, agent roles in Mission control |

## 1. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| An authorized nurse or doctor logs in and selects a synthetic patient | **Met** | e2e `foundation.spec.ts`, live acceptance |
| Voice intake recorded, transcribed locally, transcript reviewed | **Met** | Local Whisper on real audio files; transcript-review gate with low-confidence and possible-drug-name highlighting, speaker assignment, original preserved (`test_transcript_original_is_preserved_after_correction`) |
| History and relevant documents retrieved | **Met** | History agent (SQL) and documents agent (Qdrant hybrid search, Postgres re-validation); retrieval eval hit@1 = 1.0, 0 cross-patient hits |
| Evidence-backed safety flags | **Met** | Deterministic Neo4j screen over RxNorm, MED-RT, FDA EPC and label-cited edges; penicillin-class allergy alert in live run 1 |
| Privacy-filtered handoff generated | **Met** | 14 sections, every statement cited; identifier scanner plus allow-list serializers; labelled a named handoff, not anonymized |
| Doctor reviews and approves clinical documentation | **Met** | Three human gates; hash-bound approvals; doctor-only assessment, plan and prescriptions |
| Approved changes reflected in the database | **Met** | One transaction writes the note, facts, prescriptions with approvals and the finalized encounter; the note is indexed in Qdrant via the outbox (index went 103 → 104 chunks after the first live commit) |
| Agent orchestration demonstrated | **Met** | LangGraph supervisor with Postgres checkpoints, retries, step budget, interrupt and resume; eight specialist agents run as separate A2A services (tested live and automated) |
| Backend-derived execution monitoring | **Met** | Mission Control and Observability render only `agent_executions` and `workflow_events`; SSE live updates |
| Role-based restrictions | **Met** | 3 roles, 26 permissions, care-team scope; admins have no clinical access (tests and e2e) |
| Guardrail enforcement | **Met with documented limits** | See [guardrails.md](guardrails.md) |

## 2. Status by capability

**Legend.** *Implemented + tested* means it exists and is covered by automated tests or measured runs. *Partial* means it works with stated gaps. *Not implemented* means absent, with the reason given.

### Platform and data

| Capability | Status | Notes |
|---|---|---|
| Docker Compose: PostgreSQL 17.10, Qdrant 1.19.2, Neo4j 5.26.30 LTS | Implemented + tested | Loopback-only ports, health checks, named volumes, pinned images |
| Setup, start and stop scripts | Implemented + tested | `setup.ps1` builds everything from scratch, including knowledge, index and demo audio |
| Schema and migrations (Alembic 0001–0004) | Implemented + tested | 36 tables in 5 schemas plus the LangGraph `lg` schema; FHIR-aligned names; provenance on every clinical fact; soft delete via `entered_in_error` |
| DB-level guardrails | Implemented + tested | Hash-chained append-only audit; append-only version tables; immutable document content; finalized-record protection; doctor-only prescribing and approvals; least-privilege app role |
| Synthetic dataset | Implemented + tested | Synthea 4.0.0 cardiac cohort: 60 patients, 3,017 encounters, 27,344 lab/vital observations, 519 medication statements (with dose; frequency when the source gives one), 251 cardiac procedures, 474 PDF reports, 16 named scenarios (9 augmented facts, each marked) |
| Local document store (content-addressed, integrity-checked) | Implemented + tested | |

### Security

| Capability | Status | Notes |
|---|---|---|
| Argon2id, server-side sessions, idle and absolute expiry, epoch invalidation | Implemented + tested | Argon2id hashing and server-side sessions |
| CSRF header, Origin and Host allow-lists, security headers, body limits | Implemented + tested | Pure ASGI middleware |
| Rate limiting and account lockout | Implemented + tested | Database ledger |
| RBAC and patient scope; 404 on out-of-scope reads; audited denials | Implemented + tested | |
| Administrative identifiers behind admin role plus a stated reason | Implemented + tested | |
| MFA, password reset by email, SSO | Not implemented | Out of scope for a local demo; admin resets use one-time passwords |

### AI

| Capability | Status | Notes |
|---|---|---|
| Provider abstraction (`generate`, `generate_structured`, `invoke_tools`, `get_embeddings`, `health_check`, `model_info`) | Implemented + tested | |
| Ollama adapter | Implemented + tested | Loopback and local-model enforcement; cloud models refused; all schema keys required; bounded guided retries |
| Hosted-provider extension point | Partial | A guard refuses hosted providers without an approval reference; no hosted adapter is shipped (by design) |
| OpenVINO (Arc iGPU) adapter | Not implemented | Measured to be about 1.7× faster on this machine (separate benchmark); the main speed opportunity |
| Local ASR (faster-whisper `small.en`, CPU int8) | Implemented + tested | WER 2.2% on synthetic TTS audio; numpy-type and PyAV compatibility issues fixed |
| Automatic speaker diarization | Not implemented | No suitable permissively licensed local model was bundled; operators assign speakers during review. Segments that mix speakers cannot be split in the UI yet |
| Hybrid retrieval | Implemented + tested | Exact BM25 (medical tokenisation, abbreviation expansion) over Postgres chunks + bge-m3 dense search in Qdrant, fused by reciprocal rank fusion; lexical-only fallback when the dense side is down |
| Re-ranking | Implemented + tested | jina-reranker-v1-tiny-en (ONNX, CPU, ~16 ms per query) via fastembed, loaded offline; its ranking joins the fusion as a third vote. Chosen over 3 other local cross-encoders on cardiology passages; general-purpose rerankers are weak on clinical abbreviations, so its contribution is measured in the retrieval evaluation |

### Agents and tools

| Capability | Status | Notes |
|---|---|---|
| The nine agents | Implemented + tested | Care Orchestrator; Patient Registry (EMPI); Ambient Scribe; Chart Agent (EHR); Evidence Retrieval; Medication Safety CDS; Clinical Handoff Brief; Order Entry (CPOE); After-Visit Summary |
| Human gates | Implemented + tested | Four: transcript review, brief review, note and orders, safety review (plus the doctor's release of the after-visit summary) |
| Interrupt recovery and resume from checkpoint | Implemented + tested | Verified live by killing the API mid-run, plus an automated test |
| Bounded tool layer (15 tools; read/write split; caller allow-lists; id-mismatch denial) | Implemented + tested | |
| MCP server (read-only tools, session re-validation per call, run-bound patient) | Implemented + tested | MCP Python SDK v2 `MCPServer`; stdio transport |
| `encounter.create_draft` tool | Not implemented | Encounters are created through the authenticated API, which enforces identity and duplicate checks |
| A2A (agent-to-agent) | Implemented + tested | Changed at the owner's request. Each of the 8 specialist agents is its own process and A2A 1.0 JSON-RPC service (a2a-sdk 1.2.2) on 127.0.0.1:8101-8108, with an agent card. The LangGraph supervisor is the A2A client and keeps the checkpoints and the 3 human gates. Per-call capability tokens (HMAC, single use, bound to run, patient, actor, agent and skill) and input/output key allow-lists; refusals are audited. `AGENT_TRANSPORT=inprocess` remains for debugging. Limitation: the services share the `.env` secret and the database credentials (guardrails.md) |

### Knowledge

| Capability | Status | Notes |
|---|---|---|
| Versioned drug knowledge graph | Implemented + tested | Cardiology scope: 65 ingredients, 373 brand products, 82 classes, 229 MED-RT conditions, 66 interactions (some with label dose limits), 2 drug-condition cautions and 3 allergen maps, each label-quote-verified at build time; batched load; atomic release switch |
| Coverage beyond the demo set | Partial | Not a comprehensive interaction database; the UI states this on every screen |

### Frontend

| Capability | Status | Notes |
|---|---|---|
| 12 dashboard regions | Implemented + tested | React 19, Vite 8, Tailwind 4, shadcn/ui (Radix), TanStack Query/Table/Router, React Flow, Recharts; light and dark themes; colour-blind palette; phone layout |
| SSE live layer | Implemented + tested | Events only invalidate queries |

### Observability

| Capability | Status | Notes |
|---|---|---|
| Local metrics and dashboards | Implemented + tested | Agent, tool, model and retrieval latency, tokens, guardrail outcomes |
| LangSmith-compatible tracing | Implemented + tested (disabled path) | Off by default; metadata allow-list only. Not exercised against a real LangSmith endpoint (none configured) |

## 3. Verification summary

| Suite | Result |
|---|---|
| Backend, security, workflow and agent-unit tests (`uv run pytest tests`) | **191 passed**: registry (6), A2A boundary incl. peer delegation (22), cardiology knowledge screen, Synthea import invariants, hybrid retrieval, and 16 workflow tests over A2A (orders override and revise paths, unscreenable orders, safety agent down at order time) |
| Frontend unit (Vitest) | **9 passed** |
| Frontend e2e (Playwright, Edge, desktop 1440 px and phone viewport) | **12 passed**, zero console errors (incl. the registry flow) |
| Frontend typecheck and production build | Clean |
| AI evaluations | See [evaluation.md](evaluation.md) |
| Live acceptance runs (real ASR and LLMs through the HTTP API) | See section 5 |

## 4. Defects found by the verification work and fixed

These were found by tests, evaluations and live runs during the build, and are listed because they show what the checks catch.

1. **Silent false negative in medication screening.** After a new knowledge release was loaded, a running API kept querying the removed release and returned "no alerts". A live run missed warfarin + ibuprofen this way. *Fix:* the client re-checks the active release on every use and fails closed. *Test:* `test_knowledge_client_follows_release_switches`.
2. **Unverified medications skipped screening.** When extraction quarantined a medication (for example a speaker-attribution problem), it was excluded from screening. *Fix:* quarantined medication mentions are screened as "possible — unverified".
3. **Handoff narrative discarded entirely when one statement failed validation.** *Fix:* statements are validated individually, invalid ones are dropped, and the call budget is bounded at two.
4. **Mis-cited statements passed structural checks.** *Fix:* lexical-support check (a statement's content words must appear in its cited evidence).
5. **Small models omitted optional JSON keys under constrained decoding**, leading to empty symptom lists. *Fix:* every schema property is required, for all structured calls.
6. **A gate after resume left the run "running" instead of waiting.** *Fix and test:* `test_interrupted_run_resumes_from_its_checkpoint`.
7. **Misheard drug names were not flagged by ASR confidence** ("Chlorithromycin"). *Fix:* drug-vocabulary suspicion check, plus similarity-based "possible" screening.
8. **In-flight executions stayed "running" after a crash.** *Fix:* startup recovery closes them as failed (`process_restart`).
9. **Duplicate alerts** when a drug was both recorded and reported today. *Fix and test:* one alert per finding, listing every supporting patient fact.
10. **Clinical search terms in the web-server access log.** *Fix:* access logging disabled; access is recorded in the audit log without content.
11. Other fixes:
   - functional unique index on a string literal (`lower('email')`);
   - Alembic version stamp lost after a manual commit in a migration;
   - `FOR UPDATE` across an outer join;
   - numpy bools in JSONB;
   - PyAV API change;
   - upload size rule not matching the real route;
   - orphaned vectors after a database reset;
   - audit-summary `GROUP BY` positional arguments;
   - redaction label re-matching the insurance pattern;
   - missing npm peer dependencies.
12. **Handoff crashed when the medication agent itself failed.** A degraded result carries only a reason code, and the handoff expected a `statement`. Found by the A2A "agent down" test, but it affected in-process runs too (any crash of that agent). *Fix and test:* `test_a_non_critical_agent_that_is_down_degrades_and_is_stated`.
13. **A2A client blocked its event loop for about 0.5 s per call** while httpx loaded the CA bundle on Windows; eight health probes took 7.8 s and timed out. *Fix:* one cached TLS context (probes now take about 0.2 s in total).
14. **First call to each agent service cost about 4 s** of imports (live run). *Fix:* each service imports its agent code at startup. Warm overhead per call is about 50 ms (median over 78 calls in the test suite).
15. **A test left a run executing after it ended,** which then called the real model and crashed at interpreter exit. *Fix:* the test waits for the run to pause, and the session drains active runs.

### Phase 2 (cardiology) defects found by tests, evaluations and live runs

16. **A2A migration downgrade failed** (child executions referenced the deleted a2a rows); found by `--reset`. *Fix:* detach children first.
17. **Patient Registry MRN collisions** with MRNs assigned elsewhere ("max + 37"). *Fix:* desk registrations get their own MRN range with a uniqueness check.
18. **Prompt-injection check blind to the transcript:** the brief agent never sees the transcript (data minimisation), so its pre-generation check missed injected text. *Fix:* the Ambient Scribe counts instruction-like segments; the brief reports them.
19. **Synthea chronic orders often carry no dosing instructions;** the importer leaves the frequency empty instead of assuming one, and dose-limit checks say "dose not recorded" (test asserts this).
20. **Retrieval: lay language defeated BM25** (fused hit@1 0.73); a consumer-health vocabulary raised it, but a held-out set showed the overfitting (0.98 tuned vs 0.63 held-out for BM25), and the re-ranker reduced accuracy, so it is off by default.
21. **Identical alert texts for different drug pairs** in the live brief. *Fix:* alerts name the pair.
22. **"Questions" that were statements** in the live brief. *Fix:* a validator requires a question or an imperative.
23. **Outbound Hugging Face request** when an agent service loaded Whisper (revision check). *Fix:* the cached model loads strictly offline.
24. **MED-RT condition links would have flagged standard cardiac therapy** (beta-blockers "contraindicated" in heart failure, digoxin in AF). *Fix (design):* MED-RT links are non-blocking cautions; only label-quoted curated edges block.
25. **Denied symptoms read as present** ("I have never fainted" -> urgent syncope flag; "not short of breath" -> shortness of breath), found by the new-patient live run and the extraction evaluation. *Fix:* "never" and contractions are negation cues, and the cross-check matches word stems; negation accuracy 0.90 -> 1.00.

## 5. Live acceptance runs

| Run | Outcome | Duration | Key results |
|---|---|---|---|
| **Phase 2** `new_patient_palpitations` | **Encounter finalized** | 232 s | First-time patient registered live through the Patient Registry agent; empty chart stated as "no prior record"; orders screened clear; committed; summary drafted. Exposed defect 25 |
| **Phase 2** `af_warfarin_ibuprofen` (8 agent services, A2A) | **Encounter finalized** | 819 s | Returning patient matched (EMPI). 17-section SBAR brief with real record interactions from Synthea (warfarin + clopidogrel, warfarin + naproxen, digoxin + verapamil, simvastatin + verapamil "dose not recorded") and the anticoagulant/NSAID escalation. The aspirin order was **blocked** by Medication Safety via the Order Entry peer call, revised to acetaminophen, re-screened clear, committed by the Chart agent, and the after-visit summary drafted. Exposed defects 21-23 |
| `penicillin_amoxicillin` | Encounter finalized | 653 s | Penicillin-class allergy alert for patient-reported amoxicillin; intake-vs-record allergy conflict surfaced; denied cough kept as a pertinent negative. The narrative failed validation, and the deterministic sections were kept (led to fix 3) |
| `warfarin_ibuprofen` #1 | Encounter finalized | 887 s | Narrative kept (2 statements; 3 dropped by validators). Mixed-speaker ASR segments caused symptom and medication quarantine and, consequently, **no interaction alert** (led to fixes 2 and 4) |
| `warfarin_ibuprofen` #2 | Interrupted on purpose, resumed from checkpoint | n/a | Extraction fully correct; anticoagulant escalation raised. Exposed defect 1 (stale release, no alert). Resume verified live |
| `statin_macrolide` (**over A2A**) | **Encounter finalized** | **337 s** | First run with all 8 specialist agents as separate services. Whisper misheard the drug as "Chlorithromycin"; the speech agent flagged it, and the medication agent still raised the CONTRAINDICATED simvastatin + clarithromycin alert, marked as depending on unconfirmed information, with a "confirm the medication" question. Denied chest pain kept as a negative; narrative kept (2 statements dropped by validators); note committed and indexed (Qdrant 104 → 105 chunks). A2A overhead: about 4 s on each agent's first call (fixed afterwards, see defect 14), 58 ms on a warm call. Requests carried 0.3–9 KB of state per call |
| `warfarin_ibuprofen` #3 | **Encounter finalized** | **338 s** | Clean run after all fixes: MAJOR warfarin + NSAID bleeding-risk alert with FDA label citations; anticoagulant escalation raised; back pain (about a week) and denied numbness extracted; narrative kept with 4 unsupported statements dropped; note committed and indexed (Qdrant 103 → 104 chunks). The same alert was shown twice (recorded + reported warfarin), which led to fix 9 |

## 6. Known limitations

- **Phase 2 data:** Synthea is a simulation; its notes and reports are not real clinical writing, and its chronic orders often lack dosing instructions. Report PDFs are rendered from the imported values only, so they are terse.
- **Drug knowledge is cardiology-scoped:** 65 ingredients and 66 curated interactions. Allergies to drugs outside the scope (for example penicillin) are listed as not screened.
- **Extraction recall is 0.71 for symptoms** (secondary and denied symptoms are sometimes omitted); medications and allergies 1.0; nothing ungrounded. The doctor's review of the brief is the safety net.
- **Retrieval at rank 1** for lab-report questions is 0.72 on held-out wording (0.95 within the top 3); the vocabulary that helps lexical search was tuned on a separate question set and overfits it.

- **A2A trust boundary is partial.** Agent services share one secret and one database role; per-agent credentials are not implemented. The services add about 0.75 GB of RAM in total.
- **Speed.** A full intake takes 5–15 minutes on this CPU. The 9B model dominates; a 4B option and an OpenVINO adapter are the levers.
- **Benchmarks are small and synthetic:** 6 scripts, 7 retrieval queries and TTS audio. They demonstrate correctness of the pipeline and its safeguards, not clinical accuracy.
- **Identifier scanning is pattern-based** (see guardrails.md).
- **The escalation rules and the curated interaction set are demo content, not clinically reviewed.**
- **The audit chain is tamper-evident** against modification by anyone without database superuser rights; there is no external anchoring.
- **Speaker separation is manual,** and mixed-speaker segments cannot yet be split in the review UI.
- **Commits.** The repository is initialised; no git commits were made, because none were requested.
