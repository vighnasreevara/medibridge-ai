"""Local speech recognition with faster-whisper (CTranslate2, MIT). Audio never leaves the machine.

Output keeps the model's own confidence signals so uncertain words/passages can be highlighted for review:
- a word is uncertain when its probability is below ``WORD_UNCERTAIN``;
- a segment is uncertain when its average log-probability is low, it is likely non-speech, it is highly
  repetitive (a hallucination signature), or it contains uncertain words.
Speaker separation: no permissively licensed local diarization model is bundled, so segments start as
``unattributed`` and the operator assigns speaker (patient / clinician) during transcript review.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np

from ..config import get_settings
from .base import ProviderUnavailable

WORD_UNCERTAIN = 0.55
SEGMENT_LOGPROB_UNCERTAIN = -0.8
NO_SPEECH_UNCERTAIN = 0.6
COMPRESSION_SUSPECT = 2.4


@dataclass
class TranscriptionOutput:
    segments: list[dict]
    language: str | None
    duration_s: float
    model: str
    elapsed_ms: int


_lock = Lock()
SAMPLE_RATE = 16_000
MAX_AUDIO_SECONDS = 20 * 60


def decode_audio(path: Path) -> np.ndarray:
    """Decode any container PyAV supports (wav, webm/opus, ogg, mp3, m4a) to 16 kHz mono float32."""
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    total = 0
    with av.open(str(path), mode="r") as container:
        if not container.streams.audio:
            raise ProviderUnavailable("The recording contains no audio stream")
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                arr = out.to_ndarray().reshape(-1)
                chunks.append(arr)
                total += arr.size
                if total > SAMPLE_RATE * MAX_AUDIO_SECONDS:
                    raise ProviderUnavailable("Recording is longer than the 20 minute limit")
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise ProviderUnavailable("The recording is empty")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


@lru_cache
def _model(name: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ProviderUnavailable("faster-whisper is not installed") from exc
    # Once the model is cached, load it strictly offline: faster-whisper otherwise asks the Hugging Face Hub for the
    # latest revision on every load (no audio is sent, but nothing should leave the machine at runtime).
    return WhisperModel(name, device="cpu", compute_type=compute_type, cpu_threads=8, local_files_only=is_model_cached())


def model_name() -> str:
    return f"faster-whisper/{get_settings().asr_model}"


def is_model_cached() -> bool:
    from huggingface_hub import scan_cache_dir

    try:
        wanted = f"faster-whisper-{get_settings().asr_model}"
        return any(r.repo_id.endswith(wanted) for r in scan_cache_dir().repos)
    except Exception:  # noqa: BLE001 - cache dir may not exist yet
        return False


def transcribe(audio_path: Path, *, language: str | None = "en") -> TranscriptionOutput:
    s = get_settings()
    start = time.perf_counter()
    try:
        with _lock:  # one CPU transcription at a time; the model is not shared across threads safely
            model = _model(s.asr_model, s.asr_compute_type)
            samples = decode_audio(audio_path)
            segments_iter, info = model.transcribe(
                samples, language=language, beam_size=5, word_timestamps=True, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False,
                temperature=0.0)
            raw = list(segments_iter)
    except ProviderUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - decoding errors become a typed, PHI-free failure
        raise ProviderUnavailable(f"Transcription failed: {type(exc).__name__}") from exc

    segments = []
    for i, seg in enumerate(raw):
        # Cast numpy scalars to plain Python types: segments are stored as JSON.
        words = [{"w": str(w.word), "start": round(float(w.start), 2), "end": round(float(w.end), 2),
                  "p": round(float(w.probability), 3), "uncertain": bool(float(w.probability) < WORD_UNCERTAIN)}
                 for w in (seg.words or [])]
        reasons = []
        if seg.avg_logprob < SEGMENT_LOGPROB_UNCERTAIN:
            reasons.append("low_confidence")
        if seg.no_speech_prob > NO_SPEECH_UNCERTAIN:
            reasons.append("possible_non_speech")
        if seg.compression_ratio > COMPRESSION_SUSPECT:
            reasons.append("repetitive_output")
        if any(w["uncertain"] for w in words):
            reasons.append("uncertain_words")
        segments.append({
            "id": f"seg-{i + 1}", "start": round(float(seg.start), 2), "end": round(float(seg.end), 2),
            "text": str(seg.text).strip(), "speaker": "unattributed",
            "avg_logprob": round(float(seg.avg_logprob), 3), "no_speech_prob": round(float(seg.no_speech_prob), 3),
            "compression_ratio": round(float(seg.compression_ratio), 2), "words": words,
            "uncertain": bool(reasons), "uncertain_reasons": reasons,
        })
    return TranscriptionOutput(segments=segments, language=info.language, duration_s=round(float(info.duration), 2),
                               model=model_name(), elapsed_ms=int((time.perf_counter() - start) * 1000))
