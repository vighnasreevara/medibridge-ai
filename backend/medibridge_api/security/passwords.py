"""Password hashing (Argon2id) and policy.

Constant-work verification for unknown accounts and rehash-on-verify.
"""
from __future__ import annotations

import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# RFC 9106 second recommended option: Argon2id, 64 MiB, t=3, p=4.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)
_DUMMY_HASH = _hasher.hash("timing-equaliser-for-unknown-accounts")

MIN_LENGTH = 12
MAX_LENGTH = 128


def normalize(password: str) -> str:
    return unicodedata.normalize("NFC", password)


def hash_password(password: str) -> str:
    return _hasher.hash(normalize(password))


def verify(stored_hash: str | None, password: str) -> tuple[bool, str | None]:
    """Return (matches, new_hash_if_rehash_needed). Does the same work when the account does not exist."""
    try:
        _hasher.verify(stored_hash or _DUMMY_HASH, normalize(password))
    except (VerificationError, InvalidHashError):
        return False, None
    if stored_hash is None:
        return False, None
    return True, (hash_password(password) if _hasher.check_needs_rehash(stored_hash) else None)


def policy_problems(password: str, *, email: str = "", display_name: str = "") -> list[str]:
    """NIST SP 800-63B style: length, no composition rules, no obvious personal data."""
    problems: list[str] = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        problems.append(f"Use at most {MAX_LENGTH} characters.")
    lowered = password.lower()
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in lowered:
        problems.append("Do not include your email name.")
    for part in display_name.lower().split():
        if len(part) >= 4 and part in lowered:
            problems.append("Do not include your name.")
            break
    if len(set(password)) < 5:
        problems.append("Use more varied characters.")
    return problems
