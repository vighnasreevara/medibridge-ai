"""ASR output must be plain-JSON serializable (it is stored in JSONB)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from medibridge_api.config import REPO_ROOT

AUDIO = Path(REPO_ROOT) / "var" / "fixtures" / "audio" / "chest_pain_red_flags.wav"


@pytest.mark.slow
@pytest.mark.skipif(not AUDIO.exists(), reason="run scripts/make_synthetic_audio.ps1")
def test_transcription_segments_are_json_serializable():
    from medibridge_api.providers import asr

    out = asr.transcribe(AUDIO)
    json.dumps(out.segments)  # raises TypeError on numpy scalars
    assert out.segments and all(isinstance(s["uncertain"], bool) for s in out.segments)
