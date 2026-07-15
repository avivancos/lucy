import json

from lucy.observe import Tracer
from lucy.rag import (
    RagChunk,
    RagResult,
    emit_rag_retrieval_span,
    rag_span_attributes,
)
from lucy.testing import InMemoryTraceExporter


def _result() -> RagResult:
    return RagResult(
        query="Email alice@example.com about Tuesday",
        cache_hit=True,
        deadline_exceeded=False,
        chunks=[
            RagChunk(
                id="chunk-1",
                source="booking_policy",
                text="Alice can book Tuesday at alice@example.com.",
                score=0.875,
                score_components={"vector": 0.75, "lexical": 1.0},
            ),
            RagChunk(
                id="chunk-2",
                source="faq",
                text="Call +34 600 123 123 for help.",
                score=0.5,
            ),
        ],
    )


def _tracer(exporter: InMemoryTraceExporter, **kwargs) -> Tracer:
    ids = iter(
        (
            "rag-span-1",
            "00000000-0000-0000-0000-000000000002",
        )
    )
    return Tracer(
        exporters=[exporter],
        clock=lambda: 20,
        id_factory=lambda: next(ids),
        **kwargs,
    )


def test_rag_span_attributes_are_stable_ordered_json_strings():
    attributes = rag_span_attributes(_result(), include_text=True)

    assert attributes["rag.query"] == "Email alice@example.com about Tuesday"
    assert attributes["rag.cache_hit"] == "true"
    assert attributes["rag.deadline_exceeded"] == "false"
    assert attributes["rag.prompt_included_grounding_ids"] == (
        '["rag:booking_policy:chunk-1","rag:faq:chunk-2"]'
    )
    assert attributes["rag.chunks"] == (
        '[{"grounding_id":"rag:booking_policy:chunk-1","id":"chunk-1",'
        '"included_in_prompt":true,"score":0.875,"score_components":'
        '{"lexical":1.0,"vector":0.75},"source":"booking_policy","text":'
        '"Alice can book Tuesday at alice@example.com."},{"grounding_id":'
        '"rag:faq:chunk-2","id":"chunk-2","included_in_prompt":true,'
        '"score":0.5,"source":"faq","text":"Call +34 600 123 123 for help."}]'
    )
    chunks = json.loads(attributes["rag.chunks"])
    assert [chunk["id"] for chunk in chunks] == ["chunk-1", "chunk-2"]
    assert chunks[0] == {
        "grounding_id": "rag:booking_policy:chunk-1",
        "id": "chunk-1",
        "included_in_prompt": True,
        "score": 0.875,
        "score_components": {"lexical": 1.0, "vector": 0.75},
        "source": "booking_policy",
        "text": "Alice can book Tuesday at alice@example.com.",
    }
    assert "score_components" not in chunks[1]


def test_rag_span_uses_normal_redaction_and_sampling_path():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True, transcripts_enabled=True)

    emit_rag_retrieval_span(
        tracer,
        _result(),
        session_id="session-a",
        turn_id="turn-a",
        started_at_ms=10,
        ended_at_ms=20,
    )
    tracer.flush()

    assert len(exporter.events) == 1
    span = exporter.events[0]
    assert span.type == "span"
    assert span.name == "rag.retrieve"
    assert span.attributes["rag.query"] == "Email [REDACTED] about Tuesday"
    chunks = json.loads(span.attributes["rag.chunks"])
    assert chunks[0]["text"] == "Alice can book Tuesday at [REDACTED]."
    assert chunks[1]["text"] == "Call [REDACTED] for help."


def test_transcript_suppression_keeps_only_non_text_rag_evidence():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True, transcripts_enabled=False)

    emit_rag_retrieval_span(
        tracer,
        _result(),
        session_id="session-a",
        turn_id="turn-a",
        started_at_ms=10,
        ended_at_ms=20,
    )
    tracer.flush()

    span = exporter.events[0]
    assert "rag.query" not in span.attributes
    chunks = json.loads(span.attributes["rag.chunks"])
    assert all("text" not in chunk for chunk in chunks)
    assert [chunk["grounding_id"] for chunk in chunks] == [
        "rag:booking_policy:chunk-1",
        "rag:faq:chunk-2",
    ]
    assert chunks[0]["score_components"] == {"lexical": 1.0, "vector": 0.75}


def test_privacy_pass_strips_rag_text_from_direct_span_calls():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, redact_pii=True, transcripts_enabled=False)

    tracer.span(
        session_id="session-a",
        turn_id="turn-a",
        span_id="span-a",
        name="rag.retrieve",
        status="ok",
        started_at_ms=10,
        ended_at_ms=20,
        attributes=rag_span_attributes(_result(), include_text=True),
        event_id="00000000-0000-0000-0000-000000000003",
    )
    tracer.flush()

    span = exporter.events[0]
    assert "rag.query" not in span.attributes
    assert all(
        "text" not in chunk for chunk in json.loads(span.attributes["rag.chunks"])
    )


def test_unsampled_rag_span_is_not_exported():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter, sample_rate=0.0)

    emit_rag_retrieval_span(
        tracer,
        _result(),
        session_id="session-a",
        turn_id="turn-a",
        started_at_ms=10,
        ended_at_ms=20,
    )
    tracer.flush()

    assert exporter.events == []


def test_missing_telemetry_identity_is_fail_open():
    exporter = InMemoryTraceExporter()
    tracer = _tracer(exporter)

    emitted = emit_rag_retrieval_span(
        tracer,
        _result(),
        session_id="",
        turn_id=None,
        started_at_ms=10,
        ended_at_ms=20,
    )
    tracer.flush()

    assert emitted is False
    assert exporter.events == []
