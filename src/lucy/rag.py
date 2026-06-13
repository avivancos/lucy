"""Low-latency local RAG primitives."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class RagChunk:
    id: str
    source: str
    text: str
    score: float = 0.0
    score_components: Dict[str, float] = field(default_factory=dict)

    @property
    def grounding_id(self) -> str:
        return "rag:%s:%s" % (self.source, self.id)


@dataclass(frozen=True)
class RagResult:
    query: str
    chunks: List[RagChunk]
    cache_hit: bool = False
    deadline_exceeded: bool = False

    @property
    def prompt_context(self) -> str:
        return "\n".join(
            "[%s] %s" % (chunk.grounding_id, chunk.text)
            for chunk in self.chunks
        )


@dataclass
class InMemoryRagIndex:
    chunks: List[RagChunk]
    delay_ms: int = 0

    async def retrieve(self, query: str, max_chunks: int = 8) -> RagResult:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

        query_terms = {term.lower() for term in query.split()}
        scored: List[RagChunk] = []
        for chunk in self.chunks:
            text_terms = {term.strip(".,:;!?").lower() for term in chunk.text.split()}
            source_terms = {term.lower() for term in chunk.source.split("_")}
            score = len(query_terms & (text_terms | source_terms))
            if score > 0:
                scored.append(
                    RagChunk(
                        id=chunk.id,
                        source=chunk.source,
                        text=chunk.text,
                        score=float(score),
                    )
                )

        scored.sort(key=lambda chunk: (-chunk.score, chunk.id))
        return RagResult(query=query, chunks=scored[:max_chunks])


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


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _normalized_lexical_score(query: str, chunk: RagChunk) -> float:
    query_terms = {term.strip(".,:;!?").lower() for term in query.split()}
    if not query_terms:
        return 0.0
    text_terms = {term.strip(".,:;!?").lower() for term in chunk.text.split()}
    source_terms = {term.lower() for term in chunk.source.split("_")}
    return len(query_terms & (text_terms | source_terms)) / len(query_terms)


@dataclass
class HybridRagIndex:
    chunks: List[RagChunk]
    embedding_backend: LocalEmbeddingFixture
    lexical_weight: float = 0.5
    vector_weight: float = 0.5
    delay_ms: int = 0

    async def retrieve(self, query: str, max_chunks: int = 8) -> RagResult:
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)

        query_embedding = self.embedding_backend.embed_query(query)
        scored: List[RagChunk] = []
        for chunk in self.chunks:
            lexical_score = _normalized_lexical_score(query, chunk)
            vector_score = _cosine_similarity(
                query_embedding,
                self.embedding_backend.embed_chunk(chunk),
            )
            score = (
                self.lexical_weight * lexical_score
                + self.vector_weight * vector_score
            )
            if score > 0:
                scored.append(
                    RagChunk(
                        id=chunk.id,
                        source=chunk.source,
                        text=chunk.text,
                        score=score,
                        score_components={
                            "lexical": lexical_score,
                            "vector": vector_score,
                        },
                    )
                )

        scored.sort(key=lambda chunk: (-chunk.score, chunk.id))
        return RagResult(query=query, chunks=scored[:max_chunks])


@dataclass
class SpeculativeRagNode:
    index: InMemoryRagIndex
    max_chunks: int = 8
    deadline_ms: int = 50
    _cache: Dict[str, RagResult] = field(default_factory=dict)

    async def prefetch(self, query: str) -> RagResult:
        if query in self._cache:
            cached = self._cache[query]
            return RagResult(query=query, chunks=cached.chunks, cache_hit=True)

        try:
            result = await asyncio.wait_for(
                self.index.retrieve(query, max_chunks=self.max_chunks),
                timeout=self.deadline_ms / 1000,
            )
        except asyncio.TimeoutError:
            return RagResult(query=query, chunks=[], deadline_exceeded=True)

        self._cache[query] = result
        return result
