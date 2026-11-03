# Evaluation results

All evaluations use synthetic data. Accuracy is measured by explicit matching rules against hand-labelled or generated gold data, never by an LLM-as-judge. Phase 2 figures were measured on 2026-10-22 on an Intel Core Ultra 9 185H with 32 GB RAM (CPU inference, no discrete GPU), on the Synthea cardiology cohort. Each reproduction command writes a JSON report to `var/eval/`.

> These results show the pipeline behaves as designed on a synthetic benchmark. They are **not** evidence of clinical validity: the speech is synthesized, the patients are simulated, and the scenarios and questions were written by the developer.

## 1. Document retrieval: `tests.ai_eval.retrieval_eval`

**Setup.** 474 reports (896 chunks) for 60 Synthea cardiology patients. For every patient who has a report of a given kind, a plain-language clinical question is asked (never the report's title), and the rank of the first report of that kind is recorded. 9 questions x 12 patients = 108 cases per question set. Each retrieval variant runs on the same cases.

Two question sets are reported, because the lay-term vocabulary in `services/lexical.py` was written while looking at the first set's failures:

- **Tuned set:** the questions used while building that vocabulary. These numbers are optimistic.
- **Held-out set:** written afterwards, with different wording ("LV systolic function", "glycated haemoglobin", "red blood cell level"), and not used to tune anything. **This is the honest estimate.**

| Variant | Held-out hit@1 | Held-out hit@3 | Held-out MRR | Tuned hit@1 | Tuned hit@3 | Median latency |
|---|---|---|---|---|---|---|
| **Hybrid: BM25 + dense, reciprocal rank fusion (product default)** | **0.722** | **0.954** | **0.829** | 0.870 | 1.000 | 57 ms |
| Hybrid + cross-encoder re-rank (third vote) | 0.722 | 0.843 | 0.788 | 0.750 | 1.000 | 317 ms |
| Dense only (bge-m3, Qdrant) | 0.704 | 0.852 | 0.772 | 0.741 | 0.870 | 54 ms |
| BM25 only (medical tokenisation) | 0.630 | 0.694 | 0.677 | 0.981 | 1.000 | 9 ms |

Cross-patient hits returned: **0** (every candidate is re-validated in Postgres). A corrupt upload is reported in coverage as failed, never as "nothing found".

What the numbers say:

- **Fusion earns its place.** On held-out questions it beats either retriever alone on every metric. Lexical and semantic search fail on different questions: dense search misses "LV systolic function" (echo hit@3 0.17) where BM25 finds it, and BM25 misses "red blood cell level" where dense search finds it.
- **The lay-term vocabulary overfits.** BM25 drops from 0.98 hit@1 on the tuned set to 0.63 on the held-out set. Vocabulary expansion helps, but only for phrasings it anticipates.
- **Re-ranking did not help.** Four local cross-encoders were compared on cardiology passages. The best (jina-reranker-v1-tiny-en) still lowered held-out hit@3 from 0.954 to 0.843 and added about 250 ms. General-purpose rerankers do not understand clinical abbreviations. **Re-ranking is therefore off by default** (`RERANKER_MODEL` empty); reciprocal rank fusion is the ranking.
- **Weak spots that remain:** "glycated haemoglobin" (HbA1c report, hit@1 0.00 for every variant, hit@3 0.92 for hybrid) and "warfarin monitoring result" (INR, hybrid hit@1 0.33). Each patient's library holds several similar lab reports, so precision at rank 1 is genuinely hard.

## 2. Speech recognition: `tests.ai_eval.asr_eval`

- **Model:** faster-whisper `small.en`, int8, CPU.
- **Input:** 9 cardiology intake conversations voiced by Windows SAPI TTS, 20-35 s each.

| Metric | Result |
|---|---|
| Mean word error rate | **2.8%** (5 of 9 scripts at or below 2%) |
| Worst script | `ace_inhibitor_allergy` 16.2% (spoken numbers: "one hundred and sixty" for a blood-pressure reading) |
| Speed | 2.5-5 s per conversation |

TTS audio is far cleaner than real speech, so real-world error rates will be higher. Misheard drug names are still caught by the drug-vocabulary check and screened only as possible matches.

## 3. Clinical entity extraction: `tests.ai_eval.ner_eval`

`qwen3.5:9b` via Ollama, schema-constrained, prompt `ner-cardiac-v1`, on 9 hand-labelled cardiology scripts.

| Metric | Before the negation fix | **After** |
|---|---|---|
| Symptom recall | 0.679 | **0.714** |
| Negation accuracy (matched items) | 0.895 | **1.000** |
| Medication recall (incl. brand names "Viagra", "Aleve" and "water tablet") | 1.000 | **1.000** |
| Medication certainty accuracy | 1.000 | **1.000** |
| Allergy recall | 1.000 | **1.000** |
| Spurious items | 2 | 2 |
| Ungrounded items / prompt-injection echo | 0 / 0 | **0 / 0** |
| Mean time per conversation | 125 s | **76 s** |

**The negation fix.** "Never" alone was not a negation cue, and the cross-check looked for the symptom's exact name in the sentence. As a result, "I have never fainted" and "I am not short of breath" passed as present symptoms. The first live run turned the denied faint into an urgent syncope flag. Now "never" and contractions are cues, and the check falls back to the term's word stems. The validator therefore rejects the wrong flag, and the model corrects it on the guided retry. Regression tests cover both sentences.

**Remaining weakness.** The model omits some secondary or denied symptoms (knee pain, "no fever", "I have not fallen"). These are misses, not fabrications: nothing ungrounded ever became a finding. The brief and the doctor's review are the safety net; a symptom recall of 0.71 is not good enough for unattended use.

## 4. Live end-to-end acceptance: `tests.ai_eval.acceptance_live`

The full workflow runs through the HTTP API with real models and every specialist agent as a separate A2A service:

1. Patient Registry match or registration;
2. identity and consent;
3. audio upload and local ASR;
4. transcript review gate;
5. extraction, chart, evidence retrieval and medication screen;
6. the SBAR brief;
7. doctor approval;
8. orders screened agent-to-agent;
9. safety review (revise or override);
10. the Chart commit;
11. the after-visit summary.

| Scenario | Total | Outcome |
|---|---|---|
| `new_patient_palpitations` | 232 s | Encounter **finalized**. A first-time patient was **registered** through the Patient Registry agent (MRN MB7000001 from the desk-registration range, care team assigned), and the chart correctly showed no prior record ("a first visit is not a negative finding"). Orders screened **clear** agent-to-agent, then committed, and the summary drafted. **Exposed the negation defect:** "I have never fainted" became an urgent syncope flag. It is fixed (section 3) |
| `af_warfarin_ibuprofen` | 819 s | Encounter **finalized**. Returning patient matched by MRN + date of birth. The 17-section SBAR brief raised the anticoagulant/NSAID escalation and record interactions found in the Synthea data (warfarin + clopidogrel, warfarin + naproxen, digoxin + verapamil, simvastatin + verapamil with "dose not recorded"). The proposed aspirin order was **blocked** by Medication Safety (warfarin + antiplatelet), revised to acetaminophen, re-screened **clear**, committed, and the after-visit summary drafted ("Take 500 mg by mouth every 8 hours for 5 days."). Timings: extraction 402 s, brief 358 s (both slowed by memory pressure: 2.6 GB free with both Qwen models resident), order screen 0.2 s, commit and A2A overheads in milliseconds |

Issues this run exposed, now fixed:

- identical alert texts for different drug pairs (alerts now name the pair);
- statements instead of questions in "questions to clarify" (now validated);
- an outbound Hugging Face revision check when an agent service loaded Whisper (now loaded strictly offline once cached).

## 5. Automated test suites

| Suite | Result |
|---|---|
| Backend, security, workflow and agent units (`uv run pytest tests`) | 191 passed |
| Frontend unit (`npm test`) | 9 passed |
| Frontend e2e (`npx playwright test`, desktop + phone viewport) | 12 passed, 0 console errors |

## 6. Performance limits

On this CPU-only machine a full cardiology intake takes about 7-14 minutes, almost all of it in the 9B model (extraction and the brief). It is slower when free RAM is low and both Qwen models are resident. The order screen, chart commit and agent-to-agent calls take milliseconds to a few hundred milliseconds. The UI shows real per-step progress from execution records.
