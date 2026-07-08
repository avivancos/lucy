"""Turn drivers (ADR 0011): turn stt.final text into spoken directives.

`TurnDriver.run_turn` yields zero or more ``TtsSpeak`` directives (one per
flushed clause, so the first clause speaks while generation continues) then
exactly one terminal :class:`TurnDriverReport`. `CascadedTurnDriver` is the
cascaded path (STT -> LLM stream -> sentence TTS). With a
:class:`~lucy.tools.McpToolExecutor` wired in it also runs bounded tool rounds
(card 34): a mid-stream tool call is masked by one concurrent filler
utterance, the typed result is fed back, and generation resumes. Speculation
(card 36) and speech-to-speech (card 38) extend this same seam.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional, Protocol, Sequence, Union

from lucy.clock import Clock
from lucy.llm import (
    LlmMessage,
    LlmProvider,
    LlmRequest,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
    resolve_llm,
)
from lucy.providers import ModelRegistry
from lucy.settings import LatencyBudgets, LlmPricing
from lucy.speech import MIN_FLUSH_CHARS, SentenceAssembler, TtsPlanner
from lucy.tools import FillerPolicy, McpToolExecutor, ToolDef, ToolResult
from lucy.transport.schema import TtsSpeak


@dataclass(frozen=True)
class TurnDriverReport:
    assistant_text: str
    llm_ms: float
    usage: Optional[UsageReport]
    llm_cost: float = 0.0
    mcp_tools_ms: float = 0.0


DriverEvent = Union[TtsSpeak, TurnDriverReport]


class TurnDriver(Protocol):
    def run_turn(
        self, user_text: str, history: Sequence[LlmMessage]
    ) -> AsyncIterator[DriverEvent]: ...


class ToolCallsNotSupported(RuntimeError):
    """Raised when the stream requests a tool but no ``tool_executor`` is
    wired. With an executor, card 34's bounded tool rounds handle the call."""


class CascadedTurnDriver:
    def __init__(
        self,
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        clock: Clock,
        budgets: LatencyBudgets,
        pricing: Optional[LlmPricing] = None,
        min_flush_chars: int = MIN_FLUSH_CHARS,
        *,
        tool_executor: Optional[McpToolExecutor] = None,
        tools: Sequence[ToolDef] = (),
        filler_policy: Optional[FillerPolicy] = None,
        locale: str = "",
    ) -> None:
        # Validate the (provider, model) pair up front - fail fast if unregistered
        # (side effect only; we don't retain the ModelInfo).
        resolve_llm(registry, provider, model)
        self._llm = llm
        self._provider = provider
        self._model = model
        self._clock = clock
        self._budgets = budgets  # caps tool rounds via max_tool_rounds_per_turn
        self._pricing = pricing
        self._min_flush = min_flush_chars
        self._tool_executor = tool_executor
        self._tools_by_name: Dict[str, ToolDef] = {t.name: t for t in tools}
        self._filler_policy = filler_policy
        self._locale = locale

    async def run_turn(
        self, user_text: str, history: Sequence[LlmMessage]
    ) -> AsyncIterator[DriverEvent]:
        assembler = SentenceAssembler(self._min_flush)
        planner = TtsPlanner()
        messages: List[LlmMessage] = [
            *history,
            LlmMessage(role="user", content=user_text),
        ]

        assistant_text = ""
        usage: Optional[UsageReport] = None
        llm_ms = 0.0  # LLM streaming time only (excludes tool execution)
        mcp_tools_ms = 0.0
        rounds = 0

        while True:
            request = LlmRequest(
                provider=self._provider, model=self._model, messages=messages
            )
            tool_ready: Optional[ToolCallReady] = None
            buffered_at_call = False

            round_started = self._clock.monotonic()
            async for event in self._llm.stream_chat(request):
                if isinstance(event, TokenDelta):
                    assistant_text += event.text
                    for clause in assembler.feed(event.text):
                        yield planner.plan(clause)
                elif isinstance(event, (ToolCallDelta, ToolCallReady)):
                    if self._tool_executor is None:
                        raise ToolCallsNotSupported(
                            "tool calls require a tool_executor (card 34)"
                        )
                    if isinstance(event, ToolCallReady):
                        tool_ready = event
                        # Sample the buffered clause state NOW, before StreamEnd's
                        # finalize() flushes it - the filler is suppressed when a
                        # clause was already forming at the tool-call moment.
                        buffered_at_call = assembler.has_buffered()
                    # ToolCallDelta: final args arrive on ToolCallReady; nothing here
                elif isinstance(event, UsageReport):
                    usage = event
                elif isinstance(event, StreamEnd):
                    for clause in assembler.finalize():
                        yield planner.plan(clause)
                    break
            llm_ms += (self._clock.monotonic() - round_started) * 1000.0

            if tool_ready is None:
                break  # plain answer -> turn complete

            # --- one bounded tool round ---
            rounds += 1
            if rounds > self._budgets.max_tool_rounds_per_turn:
                # Cap reached: do not execute; force the model to answer verbally.
                result = ToolResult(
                    tool_key=tool_ready.name, ok=False, error_kind="budget"
                )
            elif tool_ready.name not in self._tools_by_name:
                result = ToolResult(
                    tool_key=tool_ready.name, ok=False, error_kind="unknown_tool"
                )
            else:
                assert self._tool_executor is not None  # guaranteed above
                tool = self._tools_by_name[tool_ready.name]
                filler_text = None
                if self._filler_policy is not None and not buffered_at_call:
                    filler_text = self._filler_policy.filler_for(tool, self._locale)
                # Execute CONCURRENTLY with the filler: schedule the task, speak
                # the filler while it runs, then await the result.
                exec_task = asyncio.create_task(
                    self._tool_executor.execute(tool, dict(tool_ready.arguments))
                )
                if filler_text is not None:
                    yield planner.plan(filler_text)
                result = await exec_task
                mcp_tools_ms += result.elapsed_ms

            messages = [*messages, LlmMessage(**result.to_llm_message())]

        yield TurnDriverReport(
            assistant_text=assistant_text,
            llm_ms=llm_ms,
            usage=usage,
            llm_cost=self._cost(usage),
            mcp_tools_ms=mcp_tools_ms,
        )

    def _cost(self, usage: Optional[UsageReport]) -> float:
        if usage is None or self._pricing is None:
            return 0.0
        return (usage.prompt_tokens / 1000.0) * self._pricing.prompt_per_1k + (
            usage.completion_tokens / 1000.0
        ) * self._pricing.completion_per_1k
