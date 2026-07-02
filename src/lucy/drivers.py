"""Turn drivers (ADR 0011): turn stt.final text into spoken directives.

`TurnDriver.run_turn` yields zero or more ``TtsSpeak`` directives (one per
flushed clause, so the first clause speaks while generation continues) then
exactly one terminal :class:`TurnDriverReport`. `CascadedTurnDriver` is the M1
cascaded path (STT -> LLM stream -> sentence TTS); tool rounds (card 34),
speculation (card 36), and speech-to-speech (card 38) extend this seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol, Sequence, Union

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
from lucy.transport.schema import TtsSpeak


@dataclass(frozen=True)
class TurnDriverReport:
    assistant_text: str
    llm_ms: float
    usage: Optional[UsageReport]
    llm_cost: float = 0.0


DriverEvent = Union[TtsSpeak, TurnDriverReport]


class TurnDriver(Protocol):
    def run_turn(
        self, user_text: str, history: Sequence[LlmMessage]
    ) -> AsyncIterator[DriverEvent]:
        ...


class ToolCallsNotSupported(RuntimeError):
    """M1 has no tool execution; card 34 replaces this with bounded tool rounds."""


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
    ) -> None:
        # Validate the (provider, model) pair up front - fail fast if unregistered
        # (side effect only; we don't retain the ModelInfo in M1).
        resolve_llm(registry, provider, model)
        self._llm = llm
        self._provider = provider
        self._model = model
        self._clock = clock
        # First-clause latency budget, retained for card 34 to enforce as a
        # stream timeout; not read on the M1 happy path.
        self._budgets = budgets
        self._pricing = pricing
        self._min_flush = min_flush_chars

    async def run_turn(
        self, user_text: str, history: Sequence[LlmMessage]
    ) -> AsyncIterator[DriverEvent]:
        assembler = SentenceAssembler(self._min_flush)
        planner = TtsPlanner()
        request = LlmRequest(
            provider=self._provider,
            model=self._model,
            messages=[*history, LlmMessage(role="user", content=user_text)],
        )

        started = self._clock.monotonic()
        assistant_text = ""
        usage: Optional[UsageReport] = None

        async for event in self._llm.stream_chat(request):
            if isinstance(event, TokenDelta):
                assistant_text += event.text
                for clause in assembler.feed(event.text):
                    yield planner.plan(clause)
            elif isinstance(event, (ToolCallDelta, ToolCallReady)):
                raise ToolCallsNotSupported(
                    "tool calls are not handled in M1 (card 34)"
                )
            elif isinstance(event, UsageReport):
                usage = event
            elif isinstance(event, StreamEnd):
                for clause in assembler.finalize():
                    yield planner.plan(clause)
                break

        llm_ms = (self._clock.monotonic() - started) * 1000.0
        yield TurnDriverReport(
            assistant_text=assistant_text,
            llm_ms=llm_ms,
            usage=usage,
            llm_cost=self._cost(usage),
        )

    def _cost(self, usage: Optional[UsageReport]) -> float:
        if usage is None or self._pricing is None:
            return 0.0
        return (
            (usage.prompt_tokens / 1000.0) * self._pricing.prompt_per_1k
            + (usage.completion_tokens / 1000.0) * self._pricing.completion_per_1k
        )
