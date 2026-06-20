"""Deterministic local embedding fixture for RAG contract tests (ADR 0003)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from lucy.rag import RagChunk


@dataclass
class LocalEmbeddingFixture:
    vectors: Dict[str, List[float]]

    def embed_query(self, query: str) -> List[float]:
        return self.vectors.get("query:%s" % query, _deterministic_vector(query))

    def embed_chunk(self, chunk: RagChunk) -> List[float]:
        return self.vectors.get(
            "chunk:%s" % chunk.id,
            _deterministic_vector(chunk.text),
        )


def _deterministic_vector(text: str, dimensions: int = 8) -> List[float]:
    vector = [0.0] * dimensions
    for index, character in enumerate(text.lower()):
        vector[index % dimensions] += float(ord(character) % 31)
    return vector
