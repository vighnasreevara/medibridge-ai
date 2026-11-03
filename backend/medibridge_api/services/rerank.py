"""Cross-encoder re-ranking of fused retrieval candidates (fastembed, ONNX on CPU).

The model is downloaded once at setup (``python -m medibridge_api.services.rerank --download``) into
var/models/fastembed and loaded from there at runtime; retrieval never downloads anything. If the model is not
available, retrieval keeps the fused order and says so in its timings.
"""
from __future__ import annotations

import argparse
import logging
import threading
from functools import lru_cache
from pathlib import Path

from ..config import REPO_ROOT, get_settings

CACHE = Path(REPO_ROOT) / "var" / "models" / "fastembed"
log = logging.getLogger("medibridge.rerank")
_lock = threading.Lock()


class RerankerUnavailable(Exception):
    pass


@lru_cache
def _model(name: str, offline: bool):
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    try:
        return TextCrossEncoder(model_name=name, cache_dir=str(CACHE), local_files_only=offline)
    except Exception as exc:  # noqa: BLE001 - missing files, unsupported model
        raise RerankerUnavailable(type(exc).__name__) from exc


def model_name() -> str | None:
    return get_settings().reranker_model or None


def rerank(query: str, passages: list[str]) -> list[float]:
    name = model_name()
    if not name:
        raise RerankerUnavailable("disabled")
    with _lock:  # one ONNX session per process; calls are short
        return [float(s) for s in _model(name, True).rerank(query, passages)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="download the configured model into var/models")
    args = parser.parse_args()
    name = model_name()
    if not name:
        print("RERANKER_MODEL is empty; re-ranking disabled")
        return 0
    if args.download:
        CACHE.mkdir(parents=True, exist_ok=True)
        _model(name, False)
    scores = rerank("left ventricular ejection fraction", ["Echocardiogram: LVEF 35 %.", "Lipid panel: LDL 140 mg/dL."])
    print(f"{name} ready in {CACHE}; sample scores {[round(s, 2) for s in scores]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
