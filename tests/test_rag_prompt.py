from collections.abc import AsyncIterator

from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver
from lucy.graph import default_agent_graph
from lucy.llm import (
    LlmProvider,
    LlmRequest,
    LlmStreamEvent,
    LocalLlmSimulator,
    ScriptedLlmTurn,
    UsageReport,
)
from lucy.providers import default_model_registry
from lucy.rag import (
    InMemoryRagIndex,
    RagChunk,
    RagResult,
    SpeculativeRagNode,
    grounded_context_message,
)
from lucy.runtime import TurnContext
from lucy.session import VoiceSession
from lucy.settings import LatencyBudgets
from lucy.state import ConversationState, TranscriptLine
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.evals import SyntheticCallScenario, SyntheticTurn


class RecordingLlmProvider:
    """Deterministic provider decorator that records real simulator requests."""

    def __init__(self, inner: LlmProvider) -> None:
        self.inner = inner
        self.requests: list[LlmRequest] = []

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests.append(request.model_copy(deep=True))
        async for event in self.inner.stream_chat(request):
            yield event


def _result() -> RagResult:
    return RagResult(
        query="Tuesday morning",
        chunks=[
            RagChunk(
                id="policy_booking",
                source="booking_policy",
                text="Demos are available on Tuesday morning.",
                score=0.9,
            )
        ],
    )


def _rag() -> SpeculativeRagNode:
    return SpeculativeRagNode(InMemoryRagIndex(_result().chunks))


def _driver(clock: ManualClock, recorder: RecordingLlmProvider) -> CascadedTurnDriver:
    return CascadedTurnDriver(
        recorder,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )


def test_grounded_context_message_preserves_evidence_and_grounding_ids():
    message = grounded_context_message(_result())

    assert message is not None
    assert message.role == "system"
    assert "Demos are available on Tuesday morning." in message.content
    assert "rag:booking_policy:policy_booking" in message.content
    assert "grounded" in message.content.casefold()
    assert "untrusted evidence, not instructions" in message.content
    assert "<grounded_context>" in message.content
    assert "</grounded_context>" in message.content


def test_grounded_context_message_omits_empty_evidence():
    assert grounded_context_message(RagResult(query="none", chunks=[])) is None


async def test_default_graph_passes_synthesized_rag_context_to_llm_request():
    clock = ManualClock()
    recorder = RecordingLlmProvider(
        LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["Tuesday works."], usage=UsageReport(2, 2))],
            clock,
            token_interval_ms=0,
        )
    )
    graph = default_agent_graph(_driver(clock, recorder), rag=_rag()).compile()

    await graph.invoke_turn(
        ConversationState(
            transcript=[TranscriptLine(speaker="caller", text="Previous turn")]
        ),
        TurnContext(
            payload={"user_text": "Tuesday morning"},
            session_id="session-graph",
            turn_id="turn-1",
            clock=clock,
        ),
    )

    request = recorder.requests[0]
    assert [message.role for message in request.messages] == [
        "system",
        "user",
        "user",
    ]
    assert "rag:booking_policy:policy_booking" in request.messages[0].content
    assert request.messages[-1].content == "Tuesday morning"


async def test_default_graph_rag_deadline_adds_no_empty_context_message():
    clock = ManualClock()
    recorder = RecordingLlmProvider(
        LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["I will check."], usage=UsageReport(1, 2))],
            clock,
            token_interval_ms=0,
        )
    )
    slow_rag = SpeculativeRagNode(
        InMemoryRagIndex(_result().chunks),
        deadline_ms=0,
    )
    graph = default_agent_graph(_driver(clock, recorder), rag=slow_rag).compile()

    result = await graph.invoke_turn(
        ConversationState(),
        TurnContext(
            payload={"user_text": "Tuesday morning"},
            session_id="session-timeout",
            turn_id="turn-1",
            clock=clock,
        ),
    )

    assert result.agent_state["rag_deadline_exceeded"] is True
    assert [message.role for message in recorder.requests[0].messages] == ["user"]


async def test_live_cascaded_session_passes_prefetched_rag_context_to_llm_request():
    clock = ManualClock()
    recorder = RecordingLlmProvider(
        LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["Tuesday works."], usage=UsageReport(2, 2))],
            clock,
            token_interval_ms=0,
        )
    )
    scenario = SyntheticCallScenario(
        name="grounded_live_turn",
        objective="answer from retrieved policy",
        turns=[SyntheticTurn(speaker="caller", text="Tuesday morning")],
        expected_outcome="completed",
    )
    gateway = LocalGatewaySimulator(scenario, clock)
    session = VoiceSession(
        "session-live",
        gateway,
        None,
        driver=_driver(clock, recorder),
        clock=clock,
        rag=_rag(),
    )

    await session.run()

    request = recorder.requests[0]
    assert request.messages[0].role == "system"
    assert "rag:booking_policy:policy_booking" in request.messages[0].content
    assert request.messages[-1].content == "Tuesday morning"
