"""Protected local object storage, content-addressed by SHA-256.

Files are named by hash only (no patient names or ids in paths). The store lives under ``var/``,
which is outside the web root and git-ignored; access is only through authorized API routes.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..config import get_settings

ALLOWED_EXT = {"pdf", "txt", "wav", "webm", "ogg", "mp3", "m4a"}


def _root(kind: str) -> Path:
    s = get_settings()
    root = s.document_store_dir if kind == "documents" else s.audio_store_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def put(data: bytes, ext: str, kind: str = "documents") -> tuple[str, str]:
    """Store bytes; return (storage_key, sha256). Idempotent for identical content."""
    ext = ext.lower().lstrip(".")
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type .{ext} is not accepted")
    sha = hashlib.sha256(data).hexdigest()
    key = f"{sha[:2]}/{sha}.{ext}"
    path = _root(kind) / key
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return key, sha


def path_for(key: str, kind: str = "documents") -> Path:
    root = _root(kind).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid storage key")
    return path


def read(key: str, expected_sha256: str | None = None, kind: str = "documents") -> bytes:
    data = path_for(key, kind).read_bytes()
    if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise IOError("Stored file failed its integrity check")
    return data
