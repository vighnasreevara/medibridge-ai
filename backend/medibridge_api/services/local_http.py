"""One pooled HTTP client for local services (Ollama, Qdrant).

Creating an httpx client per call rebuilds the TLS context each time, which costs ~0.5 s on Windows.
``trust_env=False`` ignores proxy environment variables, so requests to local services cannot be routed
through a system proxy by accident.
"""
from __future__ import annotations

from functools import lru_cache

import httpx


@lru_cache
def client() -> httpx.Client:
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(10.0, connect=3.0),
                        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
