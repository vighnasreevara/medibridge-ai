"""LLM provider policy and structured-output validation, with a mocked Ollama transport (no model needed)."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from medibridge_api.providers import base, ollama
from medibridge_api.services import local_http


class Extract(BaseModel):
    symptoms: list[str]


def _fake_ollama(chat_replies: list[str], show: dict | None = None):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/show":
            return httpx.Response(200, json=show or {"details": {"parameter_size": "9B"}, "capabilities": ["completion", "tools"]})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b", "digest": "abc123def4567890"}]})
        if path == "/api/chat":
            body = json.loads(request.content)
            calls.append(body)
            content = chat_replies[min(len(calls) - 1, len(chat_replies) - 1)]
            return httpx.Response(200, json={"done": True, "done_reason": "stop", "message": {"content": content},
                                             "prompt_eval_count": 10, "eval_count": 5})
        return httpx.Response(404)

    return handler, calls


@pytest.fixture
def mock_transport(monkeypatch):
    def install(handler):
        c = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(local_http, "client", lambda: c)
        monkeypatch.setattr(ollama, "http", lambda: c)
        return c
    return install


def test_valid_output_on_first_attempt(mock_transport):
    handler, calls = _fake_ollama(['{"symptoms": ["cough"]}'])
    mock_transport(handler)
    result = ollama.OllamaProvider().generate_structured([base.Message("user", "x")], Extract)
    assert result.value.symptoms == ["cough"] and result.attempts == 1
    assert calls[0]["format"]["properties"]["symptoms"]["type"] == "array"  # schema is sent to the model
    assert calls[0]["think"] is False and calls[0]["options"]["temperature"] == 0


def test_parsed_but_invalid_output_is_retried_with_problems(mock_transport):
    handler, calls = _fake_ollama(['{"symptoms": ["chest pain"]}', '{"symptoms": ["cough"]}'])
    mock_transport(handler)
    not_in_source = lambda v: [f"'{s}' is not in the source" for s in v.symptoms if s != "cough"]  # noqa: E731
    result = ollama.OllamaProvider().generate_structured([base.Message("user", "x")], Extract,
                                                         validators=[not_in_source])
    assert result.value.symptoms == ["cough"] and result.attempts == 2
    assert result.rejected == [["'chest pain' is not in the source"]]
    assert "rejected for these reasons" in calls[1]["messages"][-1]["content"]


def test_retries_are_bounded(mock_transport):
    handler, calls = _fake_ollama(['not json'])
    mock_transport(handler)
    with pytest.raises(base.StructuredOutputError) as exc:
        ollama.OllamaProvider().generate_structured([base.Message("user", "x")], Extract, max_retries=2)
    assert len(calls) == 3 and exc.value.attempts == 3


def test_cloud_model_is_refused(mock_transport):
    handler, _ = _fake_ollama(["{}"], show={"remote_host": "https://ollama.com", "details": {}})
    mock_transport(handler)
    with pytest.raises(base.ProviderRefused):
        ollama.OllamaProvider().generate([base.Message("user", "x")], model="gpt-oss:120b-cloud")


def test_non_loopback_endpoint_is_refused():
    with pytest.raises(base.ProviderRefused):
        ollama.OllamaProvider(base_url="http://10.0.0.5:11434")


def test_hosted_provider_refused_without_approval():
    from medibridge_api.providers import HostedProviderGuard

    with pytest.raises(base.ProviderRefused, match="approved deployment"):
        HostedProviderGuard("openai")


@pytest.mark.live
@pytest.mark.slow
def test_live_ollama_structured_call():
    from medibridge_api.config import get_settings

    provider = ollama.OllamaProvider()
    result = provider.generate_structured(
        [base.Message("system", "Extract symptoms literally stated. Return JSON."),
         base.Message("user", "<transcript>I have a sore throat and no fever.</transcript>")],
        Extract, model=get_settings().llm_model_light)
    assert any("throat" in s.lower() for s in result.value.symptoms)
    assert result.usage.prompt_tokens > 0
