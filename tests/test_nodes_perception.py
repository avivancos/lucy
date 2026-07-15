import json

from lucy.clock import ManualClock
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.nodes.base import CONVERSATION_STATE_PAYLOAD_KEY, as_graph_node
from lucy.nodes.perception import (
    ContextSynthesisConfig,
    ContextSynthesisNode,
    FunnelClassifierConfig,
    FunnelClassifierNode,
    LanguageDetectConfig,
    LanguageDetectNode,
    SentimentConfig,
    SentimentNode,
    SlotFillerConfig,
    SlotFillerNode,
    SlotSpec,
)
from lucy.observe import Tracer
from lucy.providers import default_model_registry
from lucy.rag import InMemoryRagIndex, RagChunk, SpeculativeRagNode
from lucy.runtime import GraphExecutor, TurnContext
from lucy.specs import FunnelStage
from lucy.state import ConversationState
from lucy.testing import InMemoryTraceExporter
from lucy.transport.schema import SessionConfigure, TtsSpeak


def _llm(clock: ManualClock, text: str, *, delay_ms: float = 0):
    return LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[text], usage=UsageReport(2, 2))],
        clock,
        token_interval_ms=delay_ms,
    )


async def test_context_synthesis_falls_back_to_retrieval_only_on_deadline():
    clock = ManualClock()
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [
                RagChunk(id="policy", source="booking", text="Tuesday is available."),
                RagChunk(
                    id="secondary",
                    source="booking",
                    text="Tuesday has a second policy.",
                ),
            ]
        )
    )
    node = ContextSynthesisNode(
        rag,
        llm=_llm(clock, "synthesized", delay_ms=100),
        provider="openai",
        model="gpt-5-mini",
        config=ContextSynthesisConfig(deadline_ms=1, max_chunks=1),
    )
    state = ConversationState()
    context = TurnContext(
        payload={
            CONVERSATION_STATE_PAYLOAD_KEY: state,
            "user_text": "Tuesday",
        },
        clock=clock,
    )

    result = await GraphExecutor([as_graph_node(node)]).run({}, context=context)

    update = result.results["context_synthesis"]
    assert "Tuesday is available." in update["agent_state"]["prompt_context"]
    assert update["agent_state"]["grounding_ids"] == ["rag:booking:policy"]


async def test_context_synthesis_emits_limited_rag_evidence_for_normal_and_fallback():
    clock = ManualClock()
    exporter = InMemoryTraceExporter()
    ids = iter(
        (
            "catalog-rag-span-1",
            "00000000-0000-0000-0000-000000000201",
            "catalog-rag-span-2",
            "00000000-0000-0000-0000-000000000202",
        )
    )
    tracer = Tracer(
        exporters=[exporter],
        clock=lambda: int(clock.monotonic() * 1000),
        id_factory=lambda: next(ids),
    )
    node = ContextSynthesisNode(
        SpeculativeRagNode(
            InMemoryRagIndex(
                [
                    RagChunk(id="a", source="booking", text="Tuesday is available."),
                    RagChunk(id="b", source="booking", text="Tuesday is discounted."),
                ]
            )
        ),
        config=ContextSynthesisConfig(max_chunks=1),
        tracer=tracer,
    )
    state = ConversationState()
    context = TurnContext(
        payload={"user_text": "Tuesday"},
        session_id="session-catalog",
        turn_id="turn-catalog",
        clock=clock,
    )

    await node(state, context)
    await node.fallback(state, context)
    tracer.flush()

    spans = [event for event in exporter.events if event.name == "rag.retrieve"]
    assert len(spans) == 2
    for span in spans:
        assert json.loads(span.attributes["rag.prompt_included_grounding_ids"]) == [
            "rag:booking:a"
        ]
        chunks = json.loads(span.attributes["rag.chunks"])
        assert [chunk["grounding_id"] for chunk in chunks] == ["rag:booking:a"]
        assert chunks[0]["included_in_prompt"] is True


async def test_slot_filler_merges_slots_and_speaks_confirmation():
    emitted = []
    node = SlotFillerNode(
        [
            SlotSpec(
                name="day",
                pattern=r"(?i)on (Tuesday)",
                required=True,
                confirm_template="Tuesday, confirmed for {day}.",
            )
        ],
        SlotFillerConfig(),
    )
    state = ConversationState(slots={"name": "Ada"})

    update = await node(
        state,
        TurnContext(
            payload={"user_text": "Book on Tuesday"},
            turn_id="turn-1",
            emit=emitted.append,
        ),
    )

    assert update["slots"] == {"name": "Ada", "day": "Tuesday"}
    assert isinstance(emitted[0], TtsSpeak)
    assert emitted[0].text == "Tuesday, confirmed for Tuesday."


async def test_sentiment_node_stores_score_from_resolved_model():
    clock = ManualClock()
    node = SentimentNode(
        _llm(clock, '{"label":"positive","confidence":0.91}'),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        SentimentConfig(),
    )

    update = await node(
        ConversationState(),
        TurnContext(payload={"user_text": "That sounds great"}, clock=clock),
    )

    sentiment = update["agent_state"]["sentiment"]
    assert sentiment["label"] == "positive"
    assert sentiment["confidence"] == 0.91
    assert sentiment["model"] == "openai/gpt-5-mini"


async def test_funnel_classifier_emits_existing_funnel_event_model():
    clock = ManualClock()
    emitted = []
    node = FunnelClassifierNode(
        _llm(clock, '{"stage":"booked","confidence":0.95}'),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        FunnelClassifierConfig(),
    )

    update = await node(
        ConversationState(slots={"day": "Tuesday"}),
        TurnContext(
            payload={"user_text": "Book it"},
            session_id="session-1",
            emit=emitted.append,
            clock=clock,
        ),
    )

    assert update["funnel_stage"] is FunnelStage.BOOKED
    assert emitted[0].stage is FunnelStage.BOOKED
    assert emitted[0].crm_payload == {"day": "Tuesday"}


async def test_language_detect_emits_session_configure_on_locale_change():
    emitted = []
    node = LanguageDetectNode(
        LanguageDetectConfig(
            supported_locales=["en-US", "es-ES"],
            min_confidence=0.8,
            stt_by_locale={"es-ES": "stt-es"},
            tts_by_locale={"es-ES": "tts-es"},
            vad="server-vad",
        )
    )

    update = await node(
        ConversationState(agent_state={"locale": "en-US"}),
        TurnContext(
            payload={"detected_locale": "es-ES", "language_confidence": 0.9},
            emit=emitted.append,
        ),
    )

    assert update["agent_state"]["locale"] == "es-ES"
    assert emitted == [SessionConfigure(stt="stt-es", tts="tts-es", vad="server-vad")]
