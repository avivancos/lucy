import pytest

from lucy.clock import ManualClock
from lucy.graph import AgentGraph
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.mcp import McpClient
from lucy.nodes.action import (
    HandoffConfig,
    HandoffNode,
    LlmNode,
    LlmNodeConfig,
    McpToolNode,
    McpToolNodeConfig,
    SayNode,
    SayNodeConfig,
)
from lucy.providers import default_model_registry
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets
from lucy.state import ConversationState
from lucy.testing import LocalMcpCommandTransport
from lucy.tools import McpToolExecutor, ToolDef, ToolProfile
from lucy.transport.schema import TtsSpeak


async def test_llm_node_streams_clauses_into_tts_speak_directives():
    clock = ManualClock()
    emitted = []
    node = LlmNode(
        LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=["First sentence. ", "Second sentence."],
                    usage=UsageReport(2, 4),
                )
            ],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        clock,
        LatencyBudgets(),
        LlmNodeConfig(min_flush_chars=1),
    )

    update = await node(
        ConversationState(),
        TurnContext(
            payload={"user_text": "Hello"},
            turn_id="turn-1",
            emit=emitted.append,
            clock=clock,
        ),
    )

    assert [item.text for item in emitted if isinstance(item, TtsSpeak)] == [
        "First sentence.",
        "Second sentence.",
    ]
    assert update["transcript"][-1].text == "First sentence. Second sentence."


async def test_mcp_tool_node_appends_audited_result_to_state():
    clock = ManualClock()
    transport = LocalMcpCommandTransport()
    tool = ToolDef(
        server="crm",
        name="book",
        description="Book a meeting",
        json_schema={},
        profile=ToolProfile(expected_latency_ms=10, deadline_ms=100),
    )
    client = McpClient(transport, allowed_tools=[tool.key])
    node = McpToolNode(
        tool,
        McpToolExecutor(client, clock),
        lambda state: {"day": state.slots["day"]},
        McpToolNodeConfig(),
    )

    update = await node(
        ConversationState(slots={"day": "Tuesday"}),
        TurnContext(payload={}, clock=clock),
    )

    assert update["tool_results"][0]["ok"] is True
    assert len(client.audit_log) == 1
    assert client.audit_log[0].allowed is True


async def test_say_node_renders_slots_with_zero_llm_calls():
    emitted = []
    node = SayNode("Booked for {day}.", SayNodeConfig())
    state = ConversationState(slots={"day": "Tuesday"})

    update = await node(
        state,
        TurnContext(turn_id="turn-1", emit=emitted.append),
    )

    assert emitted[0].text == "Booked for Tuesday."
    assert update["transcript"][-1].text == "Booked for Tuesday."
    with pytest.raises(KeyError):
        await node(ConversationState(), TurnContext())


async def test_handoff_preserves_conversation_state_across_graphs():
    async def target_node(state, ctx):
        return {
            "slots": {**state.slots, "owner": "specialist"},
            "agent_state": {**state.agent_state, "handed_off": True},
        }

    target = (
        AgentGraph[ConversationState]()
        .add_node("target", target_node)
        .set_entry("target")
        .compile()
    )
    node = HandoffNode(target, HandoffConfig())

    update = await node(
        ConversationState(slots={"day": "Tuesday"}, turns=2),
        TurnContext(payload={}, turn_id="turn-3", session_id="session-1"),
    )

    assert update["slots"] == {"day": "Tuesday", "owner": "specialist"}
    assert update["turns"] == 2
    assert update["agent_state"]["handed_off"] is True
