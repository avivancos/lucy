from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver, GraphTurnDriver
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.graph import default_agent_graph
from lucy.harness import ConversationHarness
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.providers import default_model_registry
from lucy.settings import LatencyBudgets
from lucy.state import InMemoryCheckpointStore


def _graph_driver(clock: ManualClock) -> GraphTurnDriver:
    inner = CascadedTurnDriver(
        LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=["The complete first answer should not remain in memory."],
                    usage=UsageReport(4, 9),
                ),
                ScriptedLlmTurn(
                    tokens=["The second answer completes normally."],
                    usage=UsageReport(6, 6),
                ),
            ],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    graph = default_agent_graph(inner).compile(checkpointer=InMemoryCheckpointStore())
    return GraphTurnDriver(graph, session_id="interrupted-session", clock=clock)


def _scenario() -> SyntheticCallScenario:
    return SyntheticCallScenario(
        name="interrupted_graph_memory",
        objective="retain only heard assistant speech",
        turns=[
            SyntheticTurn(speaker="caller", text="First question"),
            SyntheticTurn(speaker="caller", text="Second question"),
        ],
        expected_outcome="completed",
    )


async def test_speaking_interruption_reconciles_heard_prefix_into_graph_memory():
    clock = ManualClock()
    driver = _graph_driver(clock)

    result = await ConversationHarness().run(
        _scenario(),
        None,
        driver=driver,
        clock=clock,
        barge_in_turns={0},
    )

    heard_prefix = result.turn_records[0].assistant_text
    assert result.turn_records[0].interrupted is True
    assert heard_prefix
    assert heard_prefix != "The complete first answer should not remain in memory."
    assert [line.text for line in driver.state.transcript] == [
        "First question",
        heard_prefix,
        "Second question",
        "The second answer completes normally.",
    ]
    assert driver.state.turns == 2


async def test_thinking_interruption_is_retained_and_next_turn_id_is_monotonic():
    clock = ManualClock()
    driver = _graph_driver(clock)

    result = await ConversationHarness().run(
        _scenario(),
        None,
        driver=driver,
        clock=clock,
        vad_interrupt_turns={0},
    )

    assert result.turn_records[0].interrupted is True
    assert result.turn_records[0].assistant_text == ""
    assert [line.text for line in driver.state.transcript] == [
        "First question",
        "",
        "Second question",
        result.turn_records[1].assistant_text,
    ]
    history = await driver.checkpointer.history("interrupted-session")
    assert history
    assert {checkpoint.turn_id for checkpoint in history} == {"turn-2"}
    assert driver.state.turns == 2
