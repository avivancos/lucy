import asyncio

from lucy.rag import (
    HybridRagIndex,
    InMemoryRagIndex,
    RagChunk,
    SpeculativeRagNode,
)
from lucy.testing import LocalEmbeddingFixture


def test_in_memory_rag_retrieves_ranked_grounded_chunks():
    index = InMemoryRagIndex(
        chunks=[
            RagChunk(
                id="policy_booking",
                source="booking_policy",
                text="Demos can be booked on Tuesday morning.",
            ),
            RagChunk(
                id="pricing",
                source="pricing_faq",
                text="Pricing depends on call volume.",
            ),
        ]
    )

    result = asyncio.run(index.retrieve("book demo Tuesday", max_chunks=1))

    assert result.cache_hit is False
    assert result.chunks[0].id == "policy_booking"
    assert result.chunks[0].score > 0
    assert result.chunks[0].grounding_id == "rag:booking_policy:policy_booking"


def test_speculative_rag_cache_hit_is_explicit():
    index = InMemoryRagIndex(
        chunks=[
            RagChunk(
                id="policy_booking",
                source="booking_policy",
                text="Demos can be booked on Tuesday morning.",
            )
        ]
    )
    node = SpeculativeRagNode(index=index, max_chunks=1)

    first = asyncio.run(node.prefetch("book demo Tuesday"))
    second = asyncio.run(node.prefetch("book demo Tuesday"))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.chunks[0].grounding_id == "rag:booking_policy:policy_booking"


def test_speculative_rag_deadline_fallback_returns_empty_context():
    index = InMemoryRagIndex(
        chunks=[
            RagChunk(
                id="slow_policy",
                source="booking_policy",
                text="Demos can be booked on Tuesday morning.",
            )
        ],
        delay_ms=20,
    )
    node = SpeculativeRagNode(index=index, deadline_ms=1)

    result = asyncio.run(node.prefetch("book demo Tuesday"))

    assert result.deadline_exceeded is True
    assert result.chunks == []
    assert result.prompt_context == ""


def test_hybrid_rag_combines_lexical_and_vector_scores():
    index = HybridRagIndex(
        chunks=[
            RagChunk(
                id="lexical_booking",
                source="booking_policy",
                text="Book a demo on Tuesday morning.",
            ),
            RagChunk(
                id="semantic_booking",
                source="calendar_policy",
                text="Appointments can be reserved early in the day.",
            ),
        ],
        embedding_backend=LocalEmbeddingFixture(
            vectors={
                "query:book demo": [1.0, 0.0],
                "chunk:lexical_booking": [0.0, 1.0],
                "chunk:semantic_booking": [1.0, 0.0],
            }
        ),
        lexical_weight=0.5,
        vector_weight=0.5,
    )

    result = asyncio.run(index.retrieve("book demo", max_chunks=2))

    assert [chunk.id for chunk in result.chunks] == [
        "lexical_booking",
        "semantic_booking",
    ]
    assert result.chunks[0].score_components["lexical"] > 0
    assert result.chunks[1].score_components["lexical"] == 0
    assert result.chunks[1].score_components["vector"] == 1.0
    assert result.chunks[1].grounding_id == "rag:calendar_policy:semantic_booking"


def test_speculative_rag_cache_and_deadline_work_with_hybrid_index():
    index = HybridRagIndex(
        chunks=[
            RagChunk(
                id="semantic_booking",
                source="calendar_policy",
                text="Appointments can be reserved early in the day.",
            ),
        ],
        embedding_backend=LocalEmbeddingFixture(
            vectors={
                "query:book demo": [1.0, 0.0],
                "chunk:semantic_booking": [1.0, 0.0],
            }
        ),
    )
    node = SpeculativeRagNode(index=index, max_chunks=1)

    first = asyncio.run(node.prefetch("book demo"))
    second = asyncio.run(node.prefetch("book demo"))

    assert first.chunks[0].id == "semantic_booking"
    assert second.cache_hit is True

    slow_index = HybridRagIndex(
        chunks=index.chunks,
        embedding_backend=index.embedding_backend,
        delay_ms=20,
    )
    slow_node = SpeculativeRagNode(index=slow_index, deadline_ms=1)

    timeout_result = asyncio.run(slow_node.prefetch("book demo"))

    assert timeout_result.deadline_exceeded is True
    assert timeout_result.chunks == []
