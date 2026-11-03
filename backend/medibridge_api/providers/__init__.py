"""Provider registry. Agents call ``get_llm()``; the concrete backend comes from configuration.

Adding a provider (e.g. an OpenAI-compatible endpoint) means adding an adapter that satisfies ``LLMProvider``
and registering it here. Hosted providers must refuse to start unless ``ALLOW_HOSTED_PHI`` and
``HOSTED_PHI_APPROVAL_REF`` are both set (see ``HostedProviderGuard``).
"""
from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .base import LLMProvider, ProviderRefused


class HostedProviderGuard:
    """Extension point for hosted providers. Refuses construction without an approved deployment record."""

    def __init__(self, name: str):
        s = get_settings()
        if not (s.allow_hosted_phi and s.hosted_phi_approval_ref):
            raise ProviderRefused(
                f"Hosted provider '{name}' is disabled: clinical text may only be sent to a hosted model under an "
                "approved deployment (set ALLOW_HOSTED_PHI=true and HOSTED_PHI_APPROVAL_REF).")
        raise ProviderRefused(f"Hosted provider '{name}' is not implemented in this release.")


@lru_cache
def get_llm() -> LLMProvider:
    name = get_settings().llm_provider
    if name == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider()
    HostedProviderGuard(name)
    raise AssertionError("unreachable")
