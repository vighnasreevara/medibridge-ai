"""Capability tokens for agent-to-agent calls.

The supervisor mints one token per call. It names exactly one agent and skill, and binds the run, encounter,
patient and acting user (only the acting user for calls outside a run, such as patient registration). A token
for a peer call names the agent allowed to make it (``via``). Tokens are HMAC-SHA256 signed with MEDIBRIDGE_A2A_SECRET, expire after
A2A_TOKEN_TTL_S, and carry a nonce the receiving agent accepts once.

Limitation: every local service reads the same .env, so the secret proves "issued by a MediBridge process on
this machine", not "issued by the supervisor". The binding, expiry and single use are what stop a token for
one patient or skill being used for another.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid

from medibridge_api.config import get_settings

VERSION = 1
ISSUER = "medibridge-supervisor"


class CapabilityError(Exception):
    """Token rejected. ``code`` is safe to log and return (it never contains token contents)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key() -> bytes:
    return get_settings().medibridge_a2a_secret.get_secret_value().encode()


def _sign(body: str) -> str:
    return _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def mint(*, agent_id: str, skill_id: str, state: dict, parent_execution_id: uuid.UUID | None = None,
         ttl_s: int | None = None, via: str | None = None) -> str:
    now = int(time.time())
    claims = {"v": VERSION, "iss": ISSUER, "aud": agent_id, "skill": skill_id,
              "run": state.get("run_id"), "enc": state.get("encounter_id"), "pat": state.get("patient_id"),
              "act": state["actor_user_id"], "role": state["actor_role"], "via": via,
              "parent": str(parent_execution_id) if parent_execution_id else None,
              "iat": now, "exp": now + (ttl_s or get_settings().a2a_token_ttl_s), "nonce": secrets.token_urlsafe(16)}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(body)}"


class NonceLedger:
    """Nonces seen by this agent process, kept until their token would have expired anyway."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()

    def use(self, nonce: str, exp: int) -> bool:
        now = int(time.time())
        with self._lock:
            if len(self._seen) > 1000:
                self._seen = {n: e for n, e in self._seen.items() if e >= now}
            if nonce in self._seen:
                return False
            self._seen[nonce] = exp
            return True


def verify(token: str, *, agent_id: str, ledger: NonceLedger, now: int | None = None) -> dict:
    """Check signature, issuer, audience, expiry and single use. Returns the claims."""
    try:
        body, sig = token.split(".")
    except ValueError:
        raise CapabilityError("capability_malformed") from None
    if not hmac.compare_digest(sig, _sign(body)):
        raise CapabilityError("capability_bad_signature")
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        raise CapabilityError("capability_malformed") from None
    now = int(time.time()) if now is None else now
    if claims.get("v") != VERSION or claims.get("iss") != ISSUER:
        raise CapabilityError("capability_malformed")
    if claims.get("aud") != agent_id:
        raise CapabilityError("capability_wrong_agent")
    if not isinstance(claims.get("exp"), int) or claims["exp"] < now:
        raise CapabilityError("capability_expired")
    if claims.get("iat", now) > now + 30:
        raise CapabilityError("capability_not_yet_valid")
    if not ledger.use(str(claims.get("nonce")), claims["exp"]):
        raise CapabilityError("capability_replayed")
    return claims


CLAIM_FOR_STATE = {"run": "run_id", "enc": "encounter_id", "pat": "patient_id", "act": "actor_user_id",
                   "role": "actor_role"}


def binding_mismatches(claims: dict, state: dict) -> list[str]:
    """State keys whose value differs from what the token was issued for (unbound claims must stay absent)."""
    return [key for claim, key in CLAIM_FOR_STATE.items()
            if (str(state.get(key)) != str(claims[claim]) if claims.get(claim) is not None else key in state)]
