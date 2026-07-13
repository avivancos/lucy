"""Generation, deterministic speech, MCP action, and handoff nodes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable, List, Optional

from pydantic import Field

from lucy.clock import Clock
from lucy.graph import CompiledAgentGraph
from lucy.llm import (
    LlmMessage,
    LlmProvider,
    LlmRequest,
    StreamEnd,
    TokenDelta,
    UsageReport,
    resolve_llm,
)
from lucy.nodes.base import NodeConfig, StateUpdate
from lucy.providers import ModelRegistry
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets
from lucy.speech import MIN_FLUSH_CHARS, SentenceAssembler, TtsPlanner
from lucy.state import ConversationState, TranscriptLine
from lucy.tools import McpToolExecutor, ToolDef


class LlmNodeConfig(NodeConfig):
    deadline_ms: int = 500
    min_flush_chars: int = Field(default=MIN_FLUSH_CHARS, ge=1)


class McpToolNodeConfig(NodeConfig):
    deadline_ms: int = 1_000


class SayNodeConfig(NodeConfig):
    deadline_ms: int = 20


class HandoffConfig(NodeConfig):
    deadline_ms: int = 1_000


class LlmNode:
    name = "llm"

    def __init__(
        self,
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        clock: Clock,
        budgets: LatencyBudgets,
        config: Optional[LlmNodeConfig] = None,
    ) -> None:
        resolve_llm(registry, provider, model)
        self.llm = llm
        self.provider = provider
        self.model = model
        self.clock = clock
        self.budgets = budgets
        self.config = config or LlmNodeConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        messages: List[LlmMessage] = [
            LlmMessage(
                role="user" if line.speaker == "caller" else "assistant",
                content=line.text,
            )
            for line in state.transcript
        ]
        if not ctx.current_user_in_state:
            messages.append(
                LlmMessage(
                    role="user",
                    content=str(ctx.payload.get("user_text", "")),
                )
            )

        assembler = SentenceAssembler(self.config.min_flush_chars)
        planner = TtsPlanner()
        assistant_parts: List[str] = []
        usage: Optional[UsageReport] = None
        started = self.clock.monotonic()
        async for event in self.llm.stream_chat(
            LlmRequest(
                provider=self.provider,
                model=self.model,
                messages=messages,
            )
        ):
            if isinstance(event, TokenDelta):
                assistant_parts.append(event.text)
                for clause in assembler.feed(event.text):
                    if ctx.emit is not None:
                        ctx.emit(planner.plan(clause))
            elif isinstance(event, UsageReport):
                usage = event
            elif isinstance(event, StreamEnd) and event.finish_reason == "error":
                raise RuntimeError("LLM stream ended with an error")
        for clause in assembler.finalize():
            if ctx.emit is not None:
                ctx.emit(planner.plan(clause))

        assistant_text = "".join(assistant_parts).strip()
        llm_ms = (self.clock.monotonic() - started) * 1000.0
        return {
            "transcript": [
                *state.transcript,
                TranscriptLine(speaker="agent", text=assistant_text),
            ],
            "agent_state": {
                **state.agent_state,
                "assistant_draft": assistant_text,
                "last_turn_report": {
                    "assistant_text": assistant_text,
                    "llm_ms": llm_ms,
                    "mcp_tools_ms": 0.0,
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "cached_prompt_tokens": usage.cached_prompt_tokens if usage else 0,
                    "mcp_tool_calls": 0,
                    "rag_requests": 0,
                },
            },
        }


class McpToolNode:
    def __init__(
        self,
        tool: ToolDef,
        executor: McpToolExecutor,
        arguments: Callable[[ConversationState], dict],
        config: Optional[McpToolNodeConfig] = None,
    ) -> None:
        self.tool = tool
        self.executor = executor
        self.arguments = arguments
        self.config = config or McpToolNodeConfig(deadline_ms=tool.profile.deadline_ms)
        self.name = "tool_%s_%s" % (tool.server, tool.name)

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        dispatched = False

        def record_dispatch() -> None:
            nonlocal dispatched
            dispatched = True
            if ctx.mcp_dispatch_observer is not None:
                ctx.mcp_dispatch_observer()

        result = await self.executor.execute(
            self.tool,
            self.arguments(state),
            on_dispatch=record_dispatch,
        )
        return {
            "tool_results": [*state.tool_results, asdict(result)],
            "agent_state": {
                **state.agent_state,
                "current_mcp_tool_calls": int(
                    state.agent_state.get("current_mcp_tool_calls", 0)
                )
                + int(dispatched),
            },
        }


class SayNode:
    name = "say"

    def __init__(
        self,
        template: str,
        config: Optional[SayNodeConfig] = None,
    ) -> None:
        self.template = template
        self.config = config or SayNodeConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        text = self.template.format(**state.slots)
        if ctx.emit is not None:
            ctx.emit(TtsPlanner().plan(text))
        return {
            "transcript": [
                *state.transcript,
                TranscriptLine(speaker="agent", text=text),
            ]
        }


class HandoffNode:
    name = "handoff"

    def __init__(
        self,
        target: CompiledAgentGraph[ConversationState],
        config: Optional[HandoffConfig] = None,
    ) -> None:
        self.target = target
        self.config = config or HandoffConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        handed_off = await self.target.invoke_turn(state, ctx)
        return handed_off.model_dump(mode="python")
