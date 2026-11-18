"""Deterministic local embeddings.

No network calls and no API keys. The same text always maps to the same vector
so retrieval tests are stable. A later embedding model can replace this function
without changing the search interface.
"""

from __future__ import annotations

import hashlib
import math
import re

from benefitiq.constants import EMBEDDING_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


def embed(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    vector = [0.0] * dim
    tokens = _TOKEN.findall(text.lower())
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"
