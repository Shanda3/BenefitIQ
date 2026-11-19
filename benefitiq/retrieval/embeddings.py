"""Local open-source embeddings, with an optional OpenAI swap.

The default model is BAAI/bge-small-en-v1.5, loaded by fastembed and run on
this machine. Tests, CI, and demos do not need an API key.

When the environment variable OPENAI_API_KEY is set, vectors are requested
from OpenAI's text-embedding-3-small model at the same width. The variable's
value is never written into the repo, a fixture, or a log line. The two models
are not interchangeable: after switching, ingest the corpus again.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import httpx

from benefitiq.constants import EMBEDDING_DIM

LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
OPENAI_MODEL = "text-embedding-3-small"
OPENAI_URL = "https://api.openai.com/v1/embeddings"


class EmbeddingError(RuntimeError):
    """The embedding call could not produce a vector of the expected width."""


def _cache_dir() -> str:
    configured = os.environ.get("FASTEMBED_CACHE_PATH")
    if configured:
        return configured
    path = Path(__file__).resolve().parents[2] / "corpus" / "payers" / "cache" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@lru_cache(maxsize=1)
def _local_model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=LOCAL_MODEL, cache_dir=_cache_dir())


def _as_floats(values) -> list[float]:
    vector = [float(item) for item in values]
    if len(vector) != EMBEDDING_DIM:
        raise EmbeddingError(
            f"embedding length {len(vector)} does not match the database width {EMBEDDING_DIM}"
        )
    return vector


def _zero() -> list[float]:
    return [0.0] * EMBEDDING_DIM


def _openai_many(texts: list[str], key: str) -> list[list[float]]:
    try:
        response = httpx.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": OPENAI_MODEL, "input": texts, "dimensions": EMBEDDING_DIM},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
    except EmbeddingError:
        raise
    except Exception as exc:  # noqa: BLE001 - the message must not include the key or the body
        raise EmbeddingError("OpenAI embeddings request failed") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise EmbeddingError("OpenAI embeddings response was incomplete")
    ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
    return [_as_floats(row.get("embedding")) for row in ordered]


def _local_many(texts: list[str]) -> list[list[float]]:
    vectors = list(_local_model().embed(texts))
    if len(vectors) != len(texts):
        raise EmbeddingError("local embedding model returned the wrong number of vectors")
    return [_as_floats(vector) for vector in vectors]


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed a batch. An empty string becomes a zero vector and is not sent out."""
    if not texts:
        return []
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    pending = [(index, text) for index, text in enumerate(texts) if text and text.strip()]
    vectors: list[list[float] | None] = [_zero() if not (text and text.strip()) else None for text in texts]
    if not pending:
        return [_zero() for _text in texts]
    produced: list[list[float]] = []
    batch_texts = [text for _index, text in pending]
    for start in range(0, len(batch_texts), 64):
        batch = batch_texts[start : start + 64]
        produced.extend(_openai_many(batch, key) if key else _local_many(batch))
    for (index, _text), vector in zip(pending, produced, strict=True):
        vectors[index] = vector
    return [vector if vector is not None else _zero() for vector in vectors]


def embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    if dim != EMBEDDING_DIM:
        raise EmbeddingError("the embedding width is fixed by the database column")
    return embed_many([text])[0]


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"
