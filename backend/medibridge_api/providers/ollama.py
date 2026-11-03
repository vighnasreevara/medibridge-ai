"""Ollama adapter (default provider).

Policy enforced here, not in prompts:
- the endpoint must be loopback, and the model must be a local model (Ollama "cloud" models proxy requests
  to ollama.com and are refused), unless a hosted deployment has been explicitly approved in configuration;
- structured output is validated against the Pydantic schema and the caller's domain validators, with a
  bounded number of guided retries; it is never accepted merely because it parsed.

The structured-call shape: format schema, think off, temperature 0, completeness check.
"""
from __future__ import annotations

import json
import time
from collections.abc import Sequence
from threading import Lock
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from ..config import get_settings
from ..services.local_http import client as http
from .base import (Generation, Message, ModelInfo, ProviderRefused, ProviderUnavailable, StructuredOutputError,
                   StructuredResult, T, ToolInvocation, ToolSpec, Usage, Validator)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _ms(ns: int | None) -> int:
    return int((ns or 0) / 1_000_000)


def _usage(body: dict, model: str, digest: str | None) -> Usage:
    return Usage(model=model, model_digest=digest, prompt_tokens=body.get("prompt_eval_count") or 0,
                 completion_tokens=body.get("eval_count") or 0, load_ms=_ms(body.get("load_duration")),
                 prompt_ms=_ms(body.get("prompt_eval_duration")), generate_ms=_ms(body.get("eval_duration")),
                 total_ms=_ms(body.get("total_duration")), calls=1)


def require_all(schema: dict) -> dict:
    """Mark every property required (nullable ones stay nullable).

    With constrained decoding a model may omit any non-required key; small models then silently drop whole
    lists (e.g. no "symptoms" key at all). Requiring every key forces an explicit (possibly empty) answer.
    """
    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node

    import copy

    return fix(copy.deepcopy(schema))


def _wire(messages: Sequence[Message]) -> list[dict]:
    out = []
    for m in messages:
        item: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = m.tool_calls
        out.append(item)
    return out


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str | None = None):
        s = get_settings()
        self.base_url = (base_url or s.ollama_url).rstrip("/")
        self.default_model = s.llm_model_clinical
        self.embedding_model = s.embedding_model
        self._verified: dict[str, ModelInfo] = {}
        self._lock = Lock()
        host = urlsplit(self.base_url).hostname or ""
        if host not in LOOPBACK and not (s.allow_hosted_phi and s.hosted_phi_approval_ref):
            raise ProviderRefused(f"Ollama endpoint host '{host}' is not local and no hosted deployment is approved.")

    # ------------------------------------------------------------------ policy
    def model_info(self, model: str | None = None) -> ModelInfo:
        model = model or self.default_model
        with self._lock:
            if model in self._verified:
                return self._verified[model]
        try:
            resp = http().post(f"{self.base_url}/api/show", json={"model": model}, timeout=15)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama unreachable: {type(exc).__name__}") from exc
        if resp.status_code == 404:
            raise ProviderUnavailable(f"Model '{model}' is not installed in Ollama.")
        resp.raise_for_status()
        body = resp.json()
        remote = bool(body.get("remote_host") or body.get("remote_model")) or model.endswith(("-cloud", ":cloud"))
        s = get_settings()
        if remote and not (s.allow_hosted_phi and s.hosted_phi_approval_ref):
            raise ProviderRefused(f"Model '{model}' is a hosted (cloud) model; clinical text may not be sent to it.")
        details = body.get("details") or {}
        ctx = next((v for k, v in (body.get("model_info") or {}).items() if k.endswith("context_length")), None)
        tags = http().get(f"{self.base_url}/api/tags", timeout=10).json().get("models", [])
        digest = next((t.get("digest") for t in tags if t.get("name") in (model, f"{model}:latest")), None)
        info = ModelInfo(provider=self.name, model=model, digest=digest[:12] if digest else None,
                         parameter_size=details.get("parameter_size"), quantization=details.get("quantization_level"),
                         capabilities=body.get("capabilities") or [], context_length=ctx, is_local=not remote)
        with self._lock:
            self._verified[model] = info
        return info

    # ------------------------------------------------------------------ transport
    def _chat(self, payload: dict, timeout_s: float) -> dict:
        try:
            resp = http().post(f"{self.base_url}/api/chat", json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("Ollama request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama unreachable: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"Ollama returned HTTP {resp.status_code}")
        body = resp.json()
        if not body.get("done"):
            raise ProviderUnavailable("Ollama response was incomplete")
        return body

    # ------------------------------------------------------------------ operations
    def generate(self, messages: Sequence[Message], *, model: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 1024, timeout_s: float = 300) -> Generation:
        info = self.model_info(model)
        body = self._chat({"model": info.model, "stream": False, "think": False, "messages": _wire(messages),
                           "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192},
                           "keep_alive": "15m"}, timeout_s)
        return Generation(text=body["message"].get("content", ""), usage=_usage(body, info.model, info.digest))

    def generate_structured(self, messages: Sequence[Message], schema: type[T], *, model: str | None = None,
                            validators: Sequence[Validator] = (), max_retries: int = 2, max_tokens: int = 2048,
                            timeout_s: float = 300) -> StructuredResult[T]:
        info = self.model_info(model)
        json_schema = require_all(schema.model_json_schema())
        convo = list(messages)
        usage = Usage(model=info.model, model_digest=info.digest)
        rejected: list[list[str]] = []
        for attempt in range(1, max_retries + 2):
            body = self._chat({"model": info.model, "stream": False, "think": False, "format": json_schema,
                               "messages": _wire(convo),
                               "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": 8192},
                               "keep_alive": "15m"}, timeout_s)
            usage.add(_usage(body, info.model, info.digest))
            if body.get("done_reason") == "length":
                problems = ["The answer was cut off; produce a shorter answer that still follows the schema."]
                raw = ""
            else:
                raw = body["message"].get("content", "")
                problems = []
                value = None
                try:
                    value = schema.model_validate(json.loads(raw))
                except (json.JSONDecodeError, ValidationError) as exc:
                    problems = [f"Output did not match the schema: {str(exc)[:400]}"]
                if value is not None:
                    for validator in validators:
                        problems.extend(validator(value))
                    if not problems:
                        return StructuredResult(value=value, attempts=attempt, usage=usage, rejected=rejected)
            rejected.append(problems)
            if attempt <= max_retries:
                convo = [*messages, Message("assistant", raw or "{}"),
                         Message("user", "Your previous answer was rejected for these reasons:\n- "
                                 + "\n- ".join(problems[:12])
                                 + "\nReturn a corrected answer that follows the schema exactly. Do not add "
                                   "anything that is not supported by the source text.")]
        raise StructuredOutputError("Structured output failed validation after retries", rejected[-1],
                                    max_retries + 1, usage)

    def invoke_tools(self, messages: Sequence[Message], tools: Sequence[ToolSpec], *, model: str | None = None,
                     timeout_s: float = 300) -> ToolInvocation:
        info = self.model_info(model)
        if "tools" not in info.capabilities:
            raise ProviderRefused(f"Model '{info.model}' does not support tool calling")
        body = self._chat({"model": info.model, "stream": False, "think": False, "messages": _wire(messages),
                           "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                       "parameters": t.parameters}} for t in tools],
                           "options": {"temperature": 0, "num_ctx": 8192}, "keep_alive": "15m"}, timeout_s)
        calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments") or {}}
                 for c in body["message"].get("tool_calls") or []]
        return ToolInvocation(text=body["message"].get("content", ""), tool_calls=calls,
                              usage=_usage(body, info.model, info.digest))

    def get_embeddings(self, texts: Sequence[str], *, model: str | None = None) -> tuple[list[list[float]], Usage]:
        model = model or self.embedding_model
        info = self.model_info(model)
        start = time.perf_counter()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 16):
            try:
                resp = http().post(f"{self.base_url}/api/embed",
                                   json={"model": info.model, "input": list(texts[i:i + 16]), "keep_alive": "15m"},
                                   timeout=300)
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"Ollama unreachable: {type(exc).__name__}") from exc
            if resp.status_code >= 400:
                raise ProviderUnavailable(f"Ollama embed returned HTTP {resp.status_code}")
            vectors.extend(resp.json()["embeddings"])
        usage = Usage(model=info.model, model_digest=info.digest, total_ms=int((time.perf_counter() - start) * 1000),
                      calls=1)
        return vectors, usage

    def health_check(self) -> dict:
        try:
            version = http().get(f"{self.base_url}/api/version", timeout=3).json().get("version")
            loaded = http().get(f"{self.base_url}/api/ps", timeout=3).json().get("models", [])
            return {"ok": True, "version": version, "loaded": [m.get("name") for m in loaded]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__}
