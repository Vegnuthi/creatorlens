"""Text embeddings with fastembed (ONNX, no PyTorch), small enough for free hosting tiers.

The model is loaded exactly once per process (guarded by a lock): loading it several times
in parallel is what crashes a 512 MB server.
"""
import os
import threading
from pathlib import Path

import numpy as np

from .config import EMBEDDING_MODEL

# Keep the model files inside the project folder so a build step can download them once
CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH", str(Path(__file__).resolve().parent.parent / ".model_cache"))
THREADS = int(os.getenv("EMBED_THREADS", "0")) or None   # 1 on small servers saves memory

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from fastembed import TextEmbedding
                _model = TextEmbedding(EMBEDDING_MODEL, cache_dir=CACHE_DIR, threads=THREADS)
    return _model


def embed_passages(texts: list[str]) -> list[np.ndarray]:
    return list(_get_model().passage_embed(texts, batch_size=32))


def embed_query(text: str) -> np.ndarray:
    return next(iter(_get_model().query_embed(text)))


if __name__ == "__main__":
    # `python -m ingest.embeddings` downloads the model ahead of time (used in the Render build)
    print(embed_query("warm up").shape)
