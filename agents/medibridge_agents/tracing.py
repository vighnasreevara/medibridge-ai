"""Optional LangSmith-compatible tracing with content redaction (guardrail 9).

Disabled by default. Enabled only when LANGSMITH_TRACING=true AND an explicit LANGSMITH_ENDPOINT is configured
(an approved destination). Even then, each trace carries only allow-listed metadata: identifiers, statuses,
durations, counts and model names. Inputs/outputs are never sent, so transcripts, documents, embeddings and
patient details cannot leave the machine through tracing. The local execution dashboard does not depend on it.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from medibridge_api.config import get_settings

log = logging.getLogger("medibridge.tracing")
ALLOWED_KEYS = {"run_id", "encounter_id", "agent_id", "node", "tool_name", "status", "attempt", "duration_ms",
                "model", "prompt_tokens", "completion_tokens", "error_code", "error_class", "execution_id",
                "evidence_items", "alerts", "segments", "queries", "chunks", "version", "attempts"}


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only allow-listed keys with scalar values; drop everything else."""
    out = {}
    for k, v in data.items():
        if k in ALLOWED_KEYS and (v is None or isinstance(v, (bool, int, float))
                                  or (isinstance(v, str) and len(v) <= 80)):
            out[k] = v
    return out


@lru_cache
def _client():
    s = get_settings()
    if not (s.langsmith_tracing and s.langsmith_endpoint):
        if s.langsmith_tracing:
            log.warning("LANGSMITH_TRACING is on but no approved LANGSMITH_ENDPOINT is set; tracing stays off")
        return None
    from langsmith import Client

    return Client(api_url=s.langsmith_endpoint, api_key=s.langsmith_api_key.get_secret_value() or None,
                  hide_inputs=lambda _: {}, hide_outputs=lambda _: {})


def enabled() -> bool:
    return _client() is not None


@contextmanager
def trace(name: str, metadata: dict[str, Any]) -> Iterator[dict]:
    """Yields a dict the caller can fill with result metadata (redacted before sending)."""
    result: dict[str, Any] = {}
    client = _client()
    if client is None:
        yield result
        return
    from langsmith.run_trees import RunTree

    s = get_settings()
    rt = RunTree(name=name, run_type="chain", inputs={}, project_name=s.langsmith_project, client=client,
                 extra={"metadata": redact(metadata)})
    try:
        rt.post()
    except Exception:  # noqa: BLE001 - tracing must never break the workflow
        log.warning("trace post failed", exc_info=False)
    try:
        yield result
        rt.end(outputs={}, metadata=redact(result))
    except BaseException as exc:
        rt.end(outputs={}, error=type(exc).__name__)
        raise
    finally:
        try:
            rt.patch()
        except Exception:  # noqa: BLE001
            log.warning("trace patch failed", exc_info=False)
