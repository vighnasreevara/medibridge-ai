"""Provider-independent LLM interface.

Agents and services depend only on ``LLMProvider``; swapping Ollama for another backend means adding an adapter,
not editing agents. Structured output is never trusted because it parses: callers pass a Pydantic model (shape)
plus domain validators (meaning), and the provider retries a bounded number of times with the problems fed back.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# A domain validator inspects a parsed object and returns human-readable problems (empty list = acceptable).
Validator = Callable[[Any], list[str]]


class ProviderError(Exception):
    """Base class. ``code`` is a stable, PHI-free identifier for telemetry."""

    code = "provider_error"


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"


class ProviderRefused(ProviderError):
    """The provider configuration violates policy (e.g. a hosted/cloud model for clinical text)."""

    code = "provider_refused"


class StructuredOutputError(ProviderError):
    code = "structured_output_invalid"

    def __init__(self, message: str, problems: list[str], attempts: int, usage: "Usage"):
        super().__init__(message)
        self.problems = problems
        self.attempts = attempts
        self.usage = usage


@dataclass
class Usage:
    model: str = ""
    model_digest: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    load_ms: int = 0
    prompt_ms: int = 0
    generate_ms: int = 0
    total_ms: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        self.model = other.model or self.model
        self.model_digest = other.model_digest or self.model_digest
        for f in ("prompt_tokens", "completion_tokens", "load_ms", "prompt_ms", "generate_ms", "total_ms", "calls"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        return self


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    tool_calls: list[dict] | None = None


@dataclass
class Generation:
    text: str
    usage: Usage


@dataclass
class StructuredResult(Generic[T]):
    value: T
    attempts: int
    usage: Usage
    rejected: list[list[str]] = field(default_factory=list)  # problems from each rejected attempt


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class ToolInvocation:
    text: str
    tool_calls: list[dict]  # [{"name": str, "arguments": dict}]
    usage: Usage


@dataclass
class ModelInfo:
    provider: str
    model: str
    digest: str | None
    parameter_size: str | None
    quantization: str | None
    capabilities: list[str]
    context_length: int | None
    is_local: bool


class LLMProvider(Protocol):
    name: str

    def generate(self, messages: Sequence[Message], *, model: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 1024, timeout_s: float = 300) -> Generation: ...

    def generate_structured(self, messages: Sequence[Message], schema: type[T], *, model: str | None = None,
                            validators: Sequence[Validator] = (), max_retries: int = 2, max_tokens: int = 2048,
                            timeout_s: float = 300) -> StructuredResult[T]: ...

    def invoke_tools(self, messages: Sequence[Message], tools: Sequence[ToolSpec], *, model: str | None = None,
                     timeout_s: float = 300) -> ToolInvocation: ...

    def get_embeddings(self, texts: Sequence[str], *, model: str | None = None) -> tuple[list[list[float]], Usage]: ...

    def health_check(self) -> dict: ...

    def model_info(self, model: str | None = None) -> ModelInfo: ...
