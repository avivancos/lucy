import pytest

from lucy.clock import ManualClock
from lucy.drivers import GraphTurnDriver
from lucy.evals import SyntheticCallScenario, SyntheticTurn, booking_happy_path
from lucy.graph import AgentGraph, GraphValidationError
from lucy.harness import ConversationHarness
from lucy.state import (
    Checkpoint,
    ConversationState,
    InMemoryCheckpointStore,
    StateKeyError,
    TranscriptLine,
    checkpoint_id,
)
from lucy.transport.schema import SttFinal, TtsSpeak


def test_state_merged_replaces_only_named_keys():
    state = ConversationState(slots={"old": "kept"}, turns=1)

    merged = state.merged({"turns": 2})

    assert merged.turns == 2
    assert merged.slots == {"old": "kept"}
    assert state.turns == 1


def test_state_merged_rejects_unknown_key():
    with pytest.raises(StateKeyError):
        ConversationState().merged({"unknown": "x"})


def test_state_json_round_trip_preserves_equality():
    state = ConversationState(
        transcript=[TranscriptLine(speaker="caller", text="hello")],
        slots={"day": "Tuesday"},
    )

    assert ConversationState.model_validate_json(state.model_dump_json()) == state


def _checkpoint(
    session_id: str = "s",
    turn_id: str = "t",
    superstep: int = 1,
    *,
    state: ConversationState | None = None,
    kind: str = "superstep",
) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=checkpoint_id(session_id, turn_id, superstep),
        session_id=session_id,
        turn_id=turn_id,
        superstep=superstep,
        kind=kind,
        state=state or ConversationState(),
        created_at_ms=superstep,
    )


def test_checkpoint_id_is_deterministic():
    checkpoint = _checkpoint("session", "turn", 7)

    assert checkpoint.checkpoint_id == "session:turn:7"


async def test_load_latest_returns_newest_checkpoint():
    store = InMemoryCheckpointStore()
    await store.save(_checkpoint(superstep=1))
    await store.save(_checkpoint(superstep=2))

    latest = await store.load_latest("s")

    assert latest is not None
    assert latest.superstep == 2


async def test_history_returns_copies_in_save_order():
    store = InMemoryCheckpointStore()
    await store.save(
        _checkpoint(state=ConversationState(slots={"day": "Tuesday"}), superstep=1)
    )
    history = await store.history("s")

    history[0].state.slots["day"] = "Friday"
    fresh = await store.history("s")

    assert fresh[0].state.slots == {"day": "Tuesday"}


def _custom_graph(store: InMemoryCheckpointStore | None = None):
    async def context_synthesis(state: ConversationState, ctx):
        route = "llm"
        agent_state = {**state.agent_state, "route_taken": route}
        return {"agent_state": agent_state}

    def route(state: ConversationState) -> str:
        return str(state.agent_state["route_taken"])

    async def llm(state: ConversationState, ctx):
        text = "Graph reply to %s" % ctx.payload["user_text"]
        if ctx.emit is not None:
            ctx.emit(TtsSpeak(utterance_id="utt_%s" % ctx.turn_id, text=text))
        return {
            "transcript": [
                *state.transcript,
                TranscriptLine(speaker="agent", text=text),
            ],
            "agent_state": {
                **state.agent_state,
                "last_turn_report": {
                    "assistant_text": text,
                    "llm_ms": 0.0,
                    "llm_cost": 0.0,
                },
            },
        }

    async def finalize_funnel(state: ConversationState, ctx):
        return {"turns": state.turns + 1}

    return (
        AgentGraph[ConversationState]()
        .add_node("context_synthesis", context_synthesis)
        .add_node("llm", llm)
        .add_node("finalize_funnel", finalize_funnel)
        .add_conditional_edge("context_synthesis", route)
        .add_edge("llm", "finalize_funnel")
        .set_entry("context_synthesis")
        .compile(checkpointer=store)
    )


def test_compiled_graph_topology_hash_is_stable_and_not_object_identity():
    graph_a = _custom_graph()
    graph_b = _custom_graph()

    assert graph_a.topology_hash() == graph_b.topology_hash()
    assert "0x" not in graph_a.topology_hash()


def test_compiled_graph_topology_hash_changes_when_topology_changes():
    graph_a = _custom_graph()

    async def extra_node(state: ConversationState, ctx):
        return {"agent_state": {**state.agent_state, "extra": True}}

    graph_b = (
        AgentGraph[ConversationState]()
        .add_node("context_synthesis", lambda state, ctx: extra_node(state, ctx))
        .add_node("extra", extra_node)
        .add_edge("context_synthesis", "extra")
        .set_entry("context_synthesis")
        .compile()
    )

    assert graph_a.topology_hash() != graph_b.topology_hash()


async def test_custom_graph_conditional_edge_routes_real_harness_call():
    store = InMemoryCheckpointStore()
    driver = GraphTurnDriver(
        _custom_graph(store),
        session_id="harness_session",
        clock=ManualClock(),
    )

    result = await ConversationHarness().run(
        booking_happy_path(),
        None,
        driver=driver,
        clock=ManualClock(),
    )

    callers = [text for speaker, text in result.transcript if speaker == "caller"]
    assert callers == [
        turn.text for turn in booking_happy_path().turns if turn.speaker == "caller"
    ]
    assert result.turn_records[0].assistant_text.startswith("Graph reply")
    assert driver.state.agent_state["route_taken"] == "llm"
    assert any(checkpoint.kind == "turn_final" for checkpoint in result.checkpoints)


async def test_kill_mid_call_resume_from_load_latest_continues():
    store = InMemoryCheckpointStore()
    clock = ManualClock()
    graph = _custom_graph(store)
    driver = GraphTurnDriver(graph, session_id="s", clock=clock)

    async for _ in driver.run_turn("first half", []):
        pass

    resumed = await GraphTurnDriver.resume(
        graph,
        session_id="s",
        store=store,
        clock=clock,
    )
    async for _ in resumed.run_turn("second half", []):
        pass

    callers = [
        line.text for line in resumed.state.transcript if line.speaker == "caller"
    ]
    assert callers == ["first half", "second half"]
    assert resumed.state.turns == 2


async def test_replay_reproduces_identical_final_state():
    store = InMemoryCheckpointStore()
    harness = ConversationHarness()
    result = await harness.run(
        SyntheticCallScenario(
            name="single_turn",
            objective="one turn",
            turns=[SyntheticTurn(speaker="caller", text="hello")],
            expected_outcome="booked",
        ),
        None,
        driver=GraphTurnDriver(
            _custom_graph(store),
            session_id="harness_session",
            clock=ManualClock(),
        ),
        clock=ManualClock(),
    )

    replay = harness.replay(result.checkpoints, result.events)

    assert replay.matches_final_checkpoint is True
    assert replay.diverged_at_checkpoint_id is None
    assert replay.final_state == result.checkpoints[-1].state


async def test_replay_flags_divergence_on_tampered_events():
    store = InMemoryCheckpointStore()
    harness = ConversationHarness()
    result = await harness.run(
        SyntheticCallScenario(
            name="single_turn",
            objective="one turn",
            turns=[SyntheticTurn(speaker="caller", text="hello")],
            expected_outcome="booked",
        ),
        None,
        driver=GraphTurnDriver(
            _custom_graph(store),
            session_id="harness_session",
            clock=ManualClock(),
        ),
        clock=ManualClock(),
    )
    tampered = []
    for event in result.events:
        if isinstance(event.payload, SttFinal):
            tampered.append(
                event._replace(
                    payload=SttFinal(
                        text="tampered",
                        provider=event.payload.provider,
                        stt_ms=event.payload.stt_ms,
                    )
                )
            )
        else:
            tampered.append(event)

    replay = harness.replay(result.checkpoints, tampered)

    assert replay.matches_final_checkpoint is False
    assert replay.diverged_at_checkpoint_id == result.checkpoints[-1].checkpoint_id


def test_replay_rejects_cross_session_checkpoints():
    harness = ConversationHarness()

    with pytest.raises(GraphValidationError):
        harness.replay([_checkpoint("s1"), _checkpoint("s2")], [])
