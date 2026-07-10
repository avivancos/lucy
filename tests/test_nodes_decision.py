import pytest

from lucy.clock import ManualClock
from lucy.graph import END, AgentGraph
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.nodes.decision import (
    DisclosureConfig,
    DisclosureNode,
    GuardrailConfig,
    GuardrailNode,
    GuardrailPolicy,
    IntentRouterConfig,
    IntentRouterNode,
)
from lucy.providers import default_model_registry
from lucy.runtime import TurnContext
from lucy.state import ConversationState
from lucy.transport.schema import TtsSpeak


def _llm(clock: ManualClock, text: str):
    return LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[text], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=0,
    )


def test_intent_router_rejects_model_without_low_latency_flag():
    clock = ManualClock()

    with pytest.raises(ValueError, match="low_latency"):
        IntentRouterNode(
            {"book": "book a meeting"},
            _llm(clock, '{"intent":"book"}'),
            default_model_registry(),
            "anthropic",
            "claude-sonnet",
            IntentRouterConfig(),
        )


async def test_intent_router_label_drives_conditional_edge():
    clock = ManualClock()
    router = IntentRouterNode(
        {"book": "book a meeting", "question": "ask a question"},
        _llm(clock, '{"intent":"book"}'),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        IntentRouterConfig(),
    )

    async def booked(state, ctx):
        return {"agent_state": {**state.agent_state, "routed": "book"}}

    graph = (
        AgentGraph[ConversationState]()
        .add_node("intent_router", router)
        .add_node("book", booked)
        .add_conditional_edge(
            "intent_router", lambda state: str(state.agent_state["intent"])
        )
        .add_edge("book", END)
        .set_entry("intent_router")
        .compile()
    )

    result = await graph.invoke_turn(
        ConversationState(),
        TurnContext(payload={"user_text": "Please book"}, clock=clock),
    )

    assert result.agent_state["routed"] == "book"
    assert result.agent_state["intent_confidence"] == 1.0


async def test_intent_router_rejects_out_of_range_confidence():
    clock = ManualClock()
    router = IntentRouterNode(
        {"book": "book a meeting"},
        _llm(clock, '{"intent":"book","confidence":1.1}'),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        IntentRouterConfig(),
    )

    with pytest.raises(ValueError, match="confidence"):
        await router(
            ConversationState(),
            TurnContext(payload={"user_text": "Book"}, clock=clock),
        )


async def test_guardrail_post_replaces_draft_with_fallback_text():
    node = GuardrailNode(
        GuardrailPolicy(
            blocked_terms=["guaranteed approval"],
            fallback_text="I cannot guarantee an outcome.",
        ),
        GuardrailConfig(position="post"),
    )

    update = await node(
        ConversationState(
            agent_state={"assistant_draft": "This is guaranteed approval."}
        ),
        TurnContext(payload={}),
    )

    verdict = update["agent_state"]["guardrail"]
    assert verdict["blocked"] is True
    assert verdict["matched"] == "guaranteed approval"
    assert update["agent_state"]["assistant_draft"] == (
        "I cannot guarantee an outcome."
    )


async def test_disclosure_speaks_once_per_call():
    emitted = []
    node = DisclosureNode(DisclosureConfig(template="This call uses an AI assistant."))
    context = TurnContext(turn_id="turn-1", emit=emitted.append)

    first = await node(ConversationState(), context)
    second = await node(ConversationState(**first), context)

    assert [item.text for item in emitted if isinstance(item, TtsSpeak)] == [
        "This call uses an AI assistant."
    ]
    assert second["agent_state"]["disclosed"] is True
