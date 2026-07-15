import asyncio
import json

import pytest

from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver
from lucy.graph import (
    AgentGraph,
    GraphCycleLimitExceeded,
    GraphRouteError,
    GraphValidationError,
    default_agent_graph,
)
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.observe import Tracer
from lucy.providers import default_model_registry
from lucy.rag import InMemoryRagIndex, RagChunk, RagResult, SpeculativeRagNode
from lucy.runtime import GraphContext, GraphExecutor, GraphNode, TurnContext
from lucy.settings import GraphLimits, LatencyBudgets
from lucy.specs import FunnelStage
from lucy.state import ConversationState, InMemoryCheckpointStore, TranscriptLine
from lucy.testing import InMemoryTraceExporter
from lucy.transport.schema import TtsSpeak


def _driver(clock: ManualClock, *answers: str) -> CascadedTurnDriver:
    turns = [
        ScriptedLlmTurn(tokens=[answer], usage=UsageReport(1, 1)) for answer in answers
    ]
    return CascadedTurnDriver(
        LocalLlmSimulator(turns, clock, token_interval_ms=0),
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )


def test_turn_context_is_additive_over_graph_context():
    ctx = TurnContext(payload={})

    assert isinstance(ctx, GraphContext)
    assert ctx.session_id == ""
    assert ctx.turn_id == ""
    assert ctx.emit is None
    assert ctx.speculative is False
    assert ctx.clock.monotonic() >= 0.0


async def test_graph_executor_runs_with_injected_turn_context():
    async def first(context: GraphContext):
        return context.payload["input"].upper()

    async def second(context: GraphContext):
        return context.results["first"] + "!"

    ctx = TurnContext(payload={"existing": "kept"}, session_id="s", turn_id="t")
    result = await GraphExecutor(
        [
            GraphNode(name="first", handler=first),
            GraphNode(name="second", handler=second),
        ]
    ).run({"input": "hello"}, context=ctx)

    assert result is ctx
    assert ctx.payload == {"existing": "kept", "input": "hello"}
    assert ctx.results["second"] == "HELLO!"
    assert [event.node for event in ctx.trace] == ["first", "second"]


async def _noop(state: ConversationState, ctx: TurnContext):
    return {}


def test_duplicate_node_name_raises():
    graph = AgentGraph[ConversationState]().add_node("a", _noop)

    with pytest.raises(GraphValidationError, match="duplicate"):
        graph.add_node("a", _noop)


def test_compile_requires_known_entry():
    graph = AgentGraph[ConversationState]().add_node("a", _noop)

    with pytest.raises(GraphValidationError, match="entry"):
        graph.set_entry("missing").compile()


def test_compile_rejects_edge_to_unknown_node():
    graph = (
        AgentGraph[ConversationState]()
        .add_node("a", _noop)
        .add_edge("a", "missing")
        .set_entry("a")
    )

    with pytest.raises(GraphValidationError, match="unknown edge target"):
        graph.compile()


def test_mixed_conditional_and_static_edges_on_one_source_raise():
    graph = (
        AgentGraph[ConversationState]()
        .add_node("a", _noop)
        .add_node("b", _noop)
        .add_edge("a", "b")
        .add_conditional_edge("a", lambda state: "b")
        .set_entry("a")
    )

    with pytest.raises(GraphValidationError, match="mixes"):
        graph.compile()


def test_graph_limits_reads_env_override(monkeypatch):
    monkeypatch.setenv("LUCY_GRAPH_MAX_SUPERSTEPS_PER_TURN", "3")

    assert GraphLimits().max_supersteps_per_turn == 3


async def test_linear_graph_executes_supersteps_via_graph_executor():
    async def first(state: ConversationState, ctx: TurnContext):
        return {"agent_state": {"first": True}}

    async def second(state: ConversationState, ctx: TurnContext):
        return {"slots": {"step": "second"}}

    ctx = TurnContext(payload={}, session_id="s", turn_id="t", clock=ManualClock())
    state = await (
        AgentGraph[ConversationState]()
        .add_node("first", first)
        .add_node("second", second)
        .add_edge("first", "second")
        .set_entry("first")
        .compile()
        .invoke_turn(ConversationState(), ctx)
    )

    assert state.slots == {"step": "second"}
    assert [event.node for event in ctx.trace] == ["first", "second"]


async def test_node_fallback_and_deadline_reused_from_executor():
    async def slow(state: ConversationState, ctx: TurnContext):
        await asyncio.sleep(0.02)
        return {"slots": {"path": "slow"}}

    async def fallback(state: ConversationState, ctx: TurnContext):
        return {"slots": {"path": "fallback"}}

    ctx = TurnContext(payload={}, session_id="s", turn_id="t")
    state = await (
        AgentGraph[ConversationState]()
        .add_node("slow", slow, deadline_ms=1, fallback=fallback)
        .set_entry("slow")
        .compile()
        .invoke_turn(ConversationState(), ctx)
    )

    assert state.slots == {"path": "fallback"}
    assert ctx.trace[0].status == "fallback"


async def test_conditional_edge_routes_on_state():
    async def choose(state: ConversationState, ctx: TurnContext):
        return {"slots": {"route": "right"}}

    async def right(state: ConversationState, ctx: TurnContext):
        return {"agent_state": {"arrived": "right"}}

    state = await (
        AgentGraph[ConversationState]()
        .add_node("choose", choose)
        .add_node("right", right)
        .add_conditional_edge("choose", lambda state: state.slots["route"])
        .set_entry("choose")
        .compile()
        .invoke_turn(ConversationState(), TurnContext(payload={}))
    )

    assert state.agent_state == {"arrived": "right"}


async def test_route_to_unknown_node_raises_graph_route_error():
    graph = (
        AgentGraph[ConversationState]()
        .add_node("choose", _noop)
        .add_conditional_edge("choose", lambda state: "missing")
        .set_entry("choose")
        .compile()
    )

    with pytest.raises(GraphRouteError):
        await graph.invoke_turn(ConversationState(), TurnContext(payload={}))


async def test_cycle_capped_by_typed_graph_limits():
    graph = (
        AgentGraph[ConversationState]()
        .add_node("loop", _noop)
        .add_edge("loop", "loop")
        .set_entry("loop")
        .compile(limits=GraphLimits(max_supersteps_per_turn=3))
    )

    with pytest.raises(GraphCycleLimitExceeded):
        await graph.invoke_turn(ConversationState(), TurnContext(payload={}))


async def test_checkpoint_written_per_superstep_never_per_token():
    store = InMemoryCheckpointStore()
    graph = (
        AgentGraph[ConversationState]()
        .add_node("a", _noop)
        .add_node("b", _noop)
        .add_edge("a", "b")
        .set_entry("a")
        .compile(checkpointer=store)
    )

    await graph.invoke_turn(
        ConversationState(),
        TurnContext(payload={}, session_id="s", turn_id="t", clock=ManualClock()),
    )

    history = await store.history("s")
    assert [checkpoint.kind for checkpoint in history] == ["superstep", "superstep"]
    assert [checkpoint.superstep for checkpoint in history] == [1, 2]


async def test_default_graph_runs_synthesis_then_llm_then_finalize():
    clock = ManualClock()
    emitted: list[TtsSpeak] = []
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [
                RagChunk(
                    id="1",
                    source="booking_policy",
                    text="Tuesday morning demo slots are available.",
                )
            ]
        )
    )
    compiled = default_agent_graph(
        _driver(clock, "Happy to book Tuesday."),
        rag=rag,
        classify=lambda state: FunnelStage.BOOKED,
    ).compile()
    state = ConversationState(
        transcript=[TranscriptLine(speaker="caller", text="Tuesday morning")]
    )
    ctx = TurnContext(
        payload={"user_text": "Tuesday morning"},
        session_id="s",
        turn_id="t",
        clock=clock,
        emit=emitted.append,
    )

    result = await compiled.invoke_turn(state, ctx)

    assert [event.node for event in ctx.trace] == [
        "context_synthesis",
        "llm",
        "finalize_funnel",
    ]
    assert "Tuesday morning demo slots" in result.agent_state["prompt_context"]
    assert result.agent_state["grounding_ids"] == ["rag:booking_policy:1"]
    assert result.agent_state["last_turn_report"]["rag_requests"] == 1
    assert result.transcript[-1].text == "Happy to book Tuesday."
    assert result.turns == 1
    assert result.funnel_stage is FunnelStage.BOOKED
    assert [item.text for item in emitted] == ["Happy to book Tuesday."]


async def test_default_graph_emits_rag_inspection_span():
    clock = ManualClock()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [RagChunk(id="1", source="booking_policy", text="Tuesday is open.")]
        )
    )
    compiled = default_agent_graph(
        _driver(clock, "Booked."), rag=rag, tracer=tracer
    ).compile()
    ctx = TurnContext(
        payload={"user_text": "Tuesday"},
        session_id="session-a",
        turn_id="turn-a",
        clock=clock,
    )

    await compiled.invoke_turn(ConversationState(), ctx)
    tracer.flush()

    span = next(event for event in exporter.events if event.name == "rag.retrieve")
    assert span.session_id == "session-a"
    assert span.turn_id == "turn-a"
    assert span.status == "ok"
    assert json.loads(span.attributes["rag.prompt_included_grounding_ids"]) == [
        "rag:booking_policy:1"
    ]
    assert json.loads(span.attributes["rag.chunks"])[0]["included_in_prompt"]


async def test_synthesis_deadline_falls_back_to_retrieval_only():
    clock = ManualClock()

    class DeadlineRagSimulator(SpeculativeRagNode):
        async def prefetch(self, query: str) -> RagResult:
            return RagResult(query=query, chunks=[], deadline_exceeded=True)

    rag = DeadlineRagSimulator(InMemoryRagIndex([]))
    exporter = InMemoryTraceExporter()
    ids = iter(
        (
            "rag-deadline-span",
            "00000000-0000-0000-0000-000000000101",
        )
    )
    tracer = Tracer(
        exporters=[exporter],
        clock=lambda: int(clock.monotonic() * 1000),
        id_factory=lambda: next(ids),
    )
    compiled = default_agent_graph(
        _driver(clock, "Still answering."), rag=rag, tracer=tracer
    ).compile()

    result = await compiled.invoke_turn(
        ConversationState(transcript=[TranscriptLine(speaker="caller", text="slow")]),
        TurnContext(
            payload={"user_text": "slow"},
            session_id="session-a",
            turn_id="turn-a",
            clock=clock,
        ),
    )

    assert result.agent_state["rag_deadline_exceeded"] is True
    assert result.transcript[-1].text == "Still answering."
    tracer.flush()
    span = next(event for event in exporter.events if event.name == "rag.retrieve")
    assert span.status == "fallback"
    assert span.attributes["rag.deadline_exceeded"] == "true"


async def test_finalize_increments_turns_and_applies_classify():
    clock = ManualClock()
    compiled = default_agent_graph(
        _driver(clock, "Done."),
        classify=lambda state: FunnelStage.INTERESTED,
    ).compile()

    result = await compiled.invoke_turn(
        ConversationState(
            turns=4,
            transcript=[TranscriptLine(speaker="caller", text="hello")],
        ),
        TurnContext(payload={"user_text": "hello"}, clock=clock),
    )

    assert result.turns == 5
    assert result.funnel_stage is FunnelStage.INTERESTED
