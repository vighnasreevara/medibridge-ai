"""ASR evaluation: word error rate of local Whisper on the synthetic intake recordings.

    uv run python -m tests.ai_eval.asr_eval        (after scripts/make_synthetic_audio.ps1)

Writes var/eval/asr.json. Synthesized speech is cleaner than real speech, so these WERs are optimistic;
they verify the pipeline and detect regressions, not real-world accuracy.
"""
from __future__ import annotations

import json
import re
import sys

from medibridge_api.config import REPO_ROOT
from medibridge_api.providers import asr

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "intake_scripts.json"
AUDIO = REPO_ROOT / "var" / "fixtures" / "audio"
NUMBER_WORDS = {"two": "2", "three": "3", "four": "4", "five": "5", "forty": "40", "four hundred": "400"}


def normalize(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    for word, digit in sorted(NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{word}\b", digit, text)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    text = text.replace("don't", "do not").replace("i'm", "i am").replace("it's", "it is")
    return text.split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / max(len(ref), 1)


def main() -> int:
    scripts = json.loads(FIXTURES.read_text(encoding="utf-8"))["scripts"]
    results = []
    for s in scripts:
        path = AUDIO / f"{s['key']}.wav"
        if not path.exists():
            print(f"missing {path}; run scripts/make_synthetic_audio.ps1", file=sys.stderr)
            return 2
        out = asr.transcribe(path)
        ref = normalize(" ".join(t[1] for t in s["turns"]))
        hyp = normalize(" ".join(seg["text"] for seg in out.segments))
        results.append({"key": s["key"], "wer": round(wer(ref, hyp), 3), "audio_s": out.duration_s,
                        "elapsed_ms": out.elapsed_ms, "segments": len(out.segments),
                        "uncertain_segments": sum(seg["uncertain"] for seg in out.segments),
                        "hypothesis": " ".join(seg["text"] for seg in out.segments)})
        print(f"{s['key']:24} WER {results[-1]['wer']:.3f}  audio {out.duration_s:5.1f}s  "
              f"took {out.elapsed_ms / 1000:5.1f}s  uncertain {results[-1]['uncertain_segments']}/{len(out.segments)}")
    mean = round(sum(r["wer"] for r in results) / len(results), 3)
    report = {"model": asr.model_name(), "mean_wer": mean, "results": results,
              "caveat": "Synthetic TTS audio; real-world WER will be higher."}
    out_dir = REPO_ROOT / "var" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "asr.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"mean WER {mean}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
