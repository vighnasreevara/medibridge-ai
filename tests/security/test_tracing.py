"""Guardrail 9: tracing is off by default and never carries content."""
from __future__ import annotations

from medibridge_agents import tracing


def test_tracing_is_disabled_by_default():
    tracing._client.cache_clear()
    assert tracing.enabled() is False


def test_redaction_keeps_only_allowlisted_metadata():
    payload = {"run_id": "r1", "agent_id": "clinical_ner", "duration_ms": 1200, "model": "qwen3.5:9b",
               "transcript": "I have chest pain", "patient_name": "Bram Brightwater", "evidence": [{"text": "INR 2.6"}],
               "query": "warfarin", "status": "succeeded", "node": "x" * 200}
    out = tracing.redact(payload)
    assert out == {"run_id": "r1", "agent_id": "clinical_ner", "duration_ms": 1200, "model": "qwen3.5:9b",
                   "status": "succeeded"}


def test_trace_context_is_a_no_op_when_disabled():
    tracing._client.cache_clear()
    with tracing.trace("x", {"run_id": "r"}) as out:
        out["status"] = "ok"
    assert out == {"status": "ok"}
