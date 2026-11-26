"""Text embeddings.

Two ways to turn text into a 384-number vector, both using the SAME model so results match:
  * Local (fastembed, ONNX): used on your laptop for ingestion. Needs ~600 MB of memory.
  * Hugging Face Inference API: used by the website when HF_TOKEN is set. The free Render
    server (512 MB) can't hold the model, so it asks Hugging Face for the query vector instead.
"""
import os
import threading
import time
from pathlib import Path

import numpy as np
import requests

from .config import EMBEDDING_MODEL

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_URL = f"https://router.huggingface.co/hf-inference/models/{EMBEDDING_MODEL}/pipeline/feature-extraction"

CACHE_DIR = os.getenv("FASTEMBED_CACHE_PATH", str(Path(__file__).resolve().parent.parent / ".model_cache"))
THREADS = int(os.getenv("EMBED_THREADS", "0")) or None

_model = None
_lock = threading.Lock()


# ---------- local model (laptop) ----------

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


# ---------- Hugging Face API (website) ----------

def _to_sentence_vector(data) -> np.ndarray:
    """The API returns either one pooled vector, a batch of one, or per-token vectors."""
    arr = np.asarray(data, dtype=np.float32)
    while arr.ndim > 2:          # [1, tokens, 384] -> [tokens, 384]
        arr = arr[0]
    if arr.ndim == 2:            # [1, 384] or [tokens, 384] -> mean over rows
        arr = arr.mean(axis=0)
    if arr.shape != (384,):
        raise RuntimeError(f"Unexpected embedding shape from Hugging Face: {arr.shape}")
    return arr


def _embed_via_api(text: str) -> np.ndarray:
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    for attempt in range(4):
        resp = requests.post(HF_URL, headers=headers, json={"inputs": text}, timeout=60)
        if resp.status_code == 503 and attempt < 3:      # model is waking up on Hugging Face
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Hugging Face API error {resp.status_code}: {resp.text[:200]}")
        return _to_sentence_vector(resp.json())
    raise RuntimeError("Hugging Face model did not wake up in time, try again")


def embed_query(text: str) -> np.ndarray:
    if HF_TOKEN:
        return _embed_via_api(text)
    return next(iter(_get_model().query_embed(text)))


if __name__ == "__main__":
    print(embed_query("warm up").shape)
