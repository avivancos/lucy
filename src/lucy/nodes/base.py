"""Typed contracts shared by Lucy's prebuilt AgentGraph nodes."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from lucy.llm import LlmProvider, LlmRequest, StreamEnd, TokenDelta
from lucy.runtime import GraphContext, GraphNode, TurnContext
from lucy.state import ConversationState

CONVERSATION_STATE_PAYLOAD_KEY = "conversation_state"
StateUpdate = Dict[str, Any]
StatefulNodeHandler = Callable[[ConversationState, TurnContext], Awaitable[StateUpdate]]


class NodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_ms: int = Field(gt=0)
    retries: int = Field(default=0, ge=0)


class PrebuiltNode(Protocol):
    name: str
    config: NodeConfig

    async def __call__(
        self, state: ConversationState, ctx: TurnContext
    ) -> StateUpdate: ...


def as_graph_node(node: PrebuiltNode) -> GraphNode:
    """Adapt a stateful node for standalone GraphExecutor use."""

    async def handler(context: GraphContext) -> StateUpdate:
        state, turn_context = _state_and_context(context)
        return await node(state, turn_context)

    fallback_handler = getattr(node, "fallback", None)

    async def fallback(context: GraphContext) -> StateUpdate:
        state, turn_context = _state_and_context(context)
        handler = cast(StatefulNodeHandler, fallback_handler)
        return await handler(state, turn_context)

    return GraphNode(
        name=node.name,
        handler=handler,
        deadline_ms=node.config.deadline_ms,
        retries=node.config.retries,
        fallback=fallback if callable(fallback_handler) else None,
    )


def _state_and_context(
    context: GraphContext,
) -> tuple[ConversationState, TurnContext]:
    if not isinstance(context, TurnContext):
        raise TypeError("prebuilt nodes require TurnContext")
    state = context.payload.get(CONVERSATION_STATE_PAYLOAD_KEY)
    if not isinstance(state, ConversationState):
        raise TypeError(
            "prebuilt standalone nodes require payload['conversation_state']"
        )
    return state, cast(TurnContext, context)


async def collect_llm_text(provider: LlmProvider, request: LlmRequest) -> str:
    """Collect text from the typed streaming ABI and fail on provider errors."""
    parts: list[str] = []
    async for event in provider.stream_chat(request):
        if isinstance(event, TokenDelta):
            parts.append(event.text)
        elif isinstance(event, StreamEnd) and event.finish_reason == "error":
            raise RuntimeError("LLM stream ended with an error")
    return "".join(parts).strip()
