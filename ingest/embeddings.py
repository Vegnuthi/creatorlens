"""Text embeddings with fastembed (ONNX, no PyTorch), small enough for free hosting tiers."""
from functools import lru_cache

import numpy as np

from .config import EMBEDDING_MODEL


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(EMBEDDING_MODEL)


def embed_passages(texts: list[str]) -> list[np.ndarray]:
    return list(_model().passage_embed(texts, batch_size=32))


def embed_query(text: str) -> np.ndarray:
    # bge models expect a query instruction; fastembed's query_embed adds it
    return next(iter(_model().query_embed(text)))
