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
from enum import Enum
from typing import (
    Awaitable,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    TYPE_CHECKING,
    Union,
    runtime_checkable,
)

from lucy.clock import Clock
from lucy.llm import (
    LlmMessage,
    LlmModelNotRegistered,
    LlmProvider,
    LlmRequest,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
    resolve_llm,
)
from lucy.limits import MAX_USAGE_UNITS
from lucy.providers import (
    LOCAL_PROVIDER_NAME,
    Capability,
    ModelInfo,
    ModelRegistry,
    parse_spec_string,
)
from lucy.pricing import PriceBook, VoiceUsage
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets, LlmPricing
from lucy.speech import MIN_FLUSH_CHARS, SentenceAssembler, TtsPlanner
from lucy.state import Checkpoint, CheckpointStore, ConversationState, TranscriptLine
from lucy.state import checkpoint_id
from lucy.tools import BargeInPolicy, FillerPolicy, McpToolExecutor, ToolDef, ToolResult
from lucy.transport.schema import TtsSpeak

if TYPE_CHECKING:
    from lucy.graph import CompiledAgentGraph
    from lucy.specs import LucySpec


@dataclass(frozen=True)
class TurnDriverReport:
    assistant_text: str
    llm_ms: float
    usage: Optional[UsageReport]
    llm_cost: float = 0.0
    mcp_tools_ms: float = 0.0
    mcp_tool_calls: int = 0
    rag_requests: int = 0


DriverEvent = Union[TtsSpeak, TurnDriverReport]


def _combine_usage(
    current: Optional[UsageReport], incoming: UsageReport
) -> UsageReport:
    if current is None:
        return incoming
    return UsageReport(
        prompt_tokens=min(
            MAX_USAGE_UNITS, current.prompt_tokens + incoming.prompt_tokens
        ),
        completion_tokens=min(
            MAX_USAGE_UNITS, current.completion_tokens + incoming.completion_tokens
        ),
        cached_prompt_tokens=min(
            MAX_USAGE_UNITS,
            current.cached_prompt_tokens + incoming.cached_prompt_tokens,
        ),
    )


def _legacy_llm_cost(
    pricebook: Optional[PriceBook], usage: Optional[UsageReport]
) -> float:
    if usage is None or pricebook is None:
        return 0.0
    return pricebook.calculate(
        VoiceUsage(
            llm_prompt_tokens=usage.prompt_tokens,
            llm_cached_prompt_tokens=usage.cached_prompt_tokens,
            llm_completion_tokens=usage.completion_tokens,
        )
    ).cost.llm_cost


@dataclass(frozen=True)
class RealtimeSessionConfig:
    provider: str
    model: str
    system_prompt: str
    tools: Sequence[ToolDef] = ()
    locale: Optional[str] = None


@dataclass(frozen=True)
class RealtimeUserTranscript:
    text: str
    final: bool


@dataclass(frozen=True)
class RealtimeAssistantDelta:
    utterance_id: str
    text: str


@dataclass(frozen=True)
class RealtimeAssistantDone:
    utterance_id: str
    full_text: str


RealtimeEvent = Union[
    RealtimeUserTranscript,
    RealtimeAssistantDelta,
    RealtimeAssistantDone,
    ToolCallReady,
    UsageReport,
]


@runtime_checkable
class RealtimeSession(Protocol):
    def events(self) -> AsyncIterator[RealtimeEvent]: ...

    async def send_tool_result(self, result: ToolResult) -> None: ...

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class RealtimeProvider(Protocol):
    async def open(self, config: RealtimeSessionConfig) -> RealtimeSession: ...


@dataclass(frozen=True)
class RealtimeHooks:
    pre_turn: Sequence[Callable[[str], Awaitable[None]]] = ()
    post_turn: Sequence[Callable[[TurnDriverReport], Awaitable[None]]] = ()


class DriverKind(str, Enum):
    CASCADED = "cascaded"
    REALTIME = "realtime"


class TurnDriver(Protocol):
    def run_turn(
        self,
        user_text: str,
        history: Sequence[LlmMessage],
        *,
        turn_context: object | None = None,
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
        adopt_background_task: Optional[Callable[[asyncio.Task], None]] = None,
    ) -> None:
        # Validate the (provider, model) pair up front - fail fast if unregistered
        # (side effect only; we don't retain the ModelInfo).
        resolve_llm(registry, provider, model)
        self._llm = llm
        self._provider = provider
        self._model = model
        self._clock = clock
        self._budgets = budgets  # caps tool rounds via max_tool_rounds_per_turn
        self.pricebook = pricing.as_pricebook() if pricing is not None else None
        self._min_flush = min_flush_chars
        self._tool_executor = tool_executor
        self._tools_by_name: Dict[str, ToolDef] = {t.name: t for t in tools}
        self._filler_policy = filler_policy
        self._locale = locale
        self._adopt_background_task = adopt_background_task
        self._cache_key: Optional[str] = None

    def set_background_task_adopter(
        self, adopt_background_task: Optional[Callable[[asyncio.Task], None]]
    ) -> None:
        self._adopt_background_task = adopt_background_task

    def set_cache_key(self, cache_key: Optional[str]) -> None:
        self._cache_key = cache_key

    async def run_turn(
        self,
        user_text: str,
        history: Sequence[LlmMessage],
        *,
        turn_context: object | None = None,
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
        mcp_tool_calls = 0
        rounds = 0

        def record_mcp_dispatch() -> None:
            nonlocal mcp_tool_calls
            mcp_tool_calls += 1
            observer = (
                turn_context.mcp_dispatch_observer
                if isinstance(turn_context, TurnContext)
                else None
            )
            if observer is not None:
                observer()

        while True:
            request = LlmRequest(
                provider=self._provider,
                model=self._model,
                messages=messages,
                cache_key=self._cache_key,
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
                    usage = _combine_usage(usage, event)
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
                if getattr(turn_context, "speculative", False):
                    promoted = getattr(turn_context, "promoted")
                    await promoted.wait()
                assert self._tool_executor is not None  # guaranteed above
                tool = self._tools_by_name[tool_ready.name]
                filler_text = None
                if self._filler_policy is not None and not buffered_at_call:
                    filler_text = self._filler_policy.filler_for(tool, self._locale)
                # Execute CONCURRENTLY with the filler: schedule the task, speak
                # the filler while it runs, then await the result.
                exec_task = asyncio.create_task(
                    self._tool_executor.execute(
                        tool,
                        dict(tool_ready.arguments),
                        on_dispatch=record_mcp_dispatch,
                    ),
                    name="tool:%s" % tool.key,
                )
                if filler_text is not None:
                    yield planner.plan(filler_text)
                if tool.profile.on_barge_in == BargeInPolicy.RUN_TO_COMPLETION:
                    try:
                        result = await asyncio.shield(exec_task)
                    except asyncio.CancelledError:
                        if self._adopt_background_task is not None:
                            self._adopt_background_task(exec_task)
                        raise
                else:
                    result = await exec_task
                mcp_tools_ms += result.elapsed_ms

            messages = [*messages, LlmMessage(**result.to_llm_message())]

        yield TurnDriverReport(
            assistant_text=assistant_text,
            llm_ms=llm_ms,
            usage=usage,
            llm_cost=_legacy_llm_cost(self.pricebook, usage),
            mcp_tools_ms=mcp_tools_ms,
            mcp_tool_calls=mcp_tool_calls,
        )


def resolve_realtime(registry: ModelRegistry, provider: str, model: str) -> ModelInfo:
    info = registry.get(provider, model)
    if info is None or Capability.REALTIME not in info.capabilities:
        raise LlmModelNotRegistered(
            "no realtime-capable model %r/%r in the registry" % (provider, model)
        )
    return info


def select_driver(spec: "LucySpec", registry: ModelRegistry) -> DriverKind:
    provider, model = parse_spec_string(spec.voice.llm_provider)
    if provider == LOCAL_PROVIDER_NAME:
        return DriverKind.CASCADED
    assert model is not None
    info = registry.get(provider, model)
    if info is None:
        raise LlmModelNotRegistered("model %s/%s is not registered" % (provider, model))
    if Capability.REALTIME in info.capabilities:
        return DriverKind.REALTIME
    if Capability.LLM in info.capabilities:
        return DriverKind.CASCADED
    capabilities = ", ".join(capability.value for capability in info.capabilities)
    raise LlmModelNotRegistered(
        "model %s/%s has capabilities [%s], not llm or realtime"
        % (provider, model, capabilities)
    )


class RealtimeTurnDriver:
    def __init__(
        self,
        provider: RealtimeProvider,
        config: RealtimeSessionConfig,
        registry: ModelRegistry,
        clock: Clock,
        budgets: LatencyBudgets,
        tool_executor: Optional[McpToolExecutor] = None,
        tools: Sequence[ToolDef] = (),
        hooks: RealtimeHooks = RealtimeHooks(),
        pricing: Optional[LlmPricing] = None,
    ) -> None:
        resolve_realtime(registry, config.provider, config.model)
        self._provider = provider
        self._config = config
        self._clock = clock
        self._budgets = budgets
        self._tool_executor = tool_executor
        self._tools_by_name = {tool.name: tool for tool in tools}
        self._hooks = hooks
        self.pricebook = pricing.as_pricebook() if pricing is not None else None
        self._session: Optional[RealtimeSession] = None
        self.last_voiced_text = ""

    async def _open(self) -> RealtimeSession:
        if self._session is None:
            self._session = await self._provider.open(self._config)
        return self._session

    async def run_turn(
        self,
        user_text: str,
        history: Sequence[LlmMessage],
        *,
        turn_context: object | None = None,
    ) -> AsyncIterator[DriverEvent]:
        del history
        session = await self._open()
        self.last_voiced_text = ""
        usage: Optional[UsageReport] = None
        assistant_done: Optional[RealtimeAssistantDone] = None
        mcp_tools_ms = 0.0
        mcp_tool_calls = 0
        rounds = 0
        started = self._clock.monotonic()

        def record_mcp_dispatch() -> None:
            nonlocal mcp_tool_calls
            mcp_tool_calls += 1
            observer = (
                turn_context.mcp_dispatch_observer
                if isinstance(turn_context, TurnContext)
                else None
            )
            if observer is not None:
                observer()

        for pre_hook in self._hooks.pre_turn:
            await pre_hook(user_text)
        try:
            async for event in session.events():
                if isinstance(event, RealtimeAssistantDelta):
                    self.last_voiced_text += event.text
                elif isinstance(event, UsageReport):
                    usage = event
                elif isinstance(event, ToolCallReady):
                    rounds += 1
                    tool = self._tools_by_name.get(event.name)
                    if rounds > self._budgets.max_tool_rounds_per_turn:
                        result = ToolResult(
                            tool_key=event.name, ok=False, error_kind="budget"
                        )
                    elif tool is None or self._tool_executor is None:
                        result = ToolResult(
                            tool_key=event.name,
                            ok=False,
                            error_kind="unknown_tool",
                        )
                    else:
                        result = await self._tool_executor.execute(
                            tool,
                            dict(event.arguments),
                            on_dispatch=record_mcp_dispatch,
                        )
                        mcp_tools_ms += result.elapsed_ms
                    await session.send_tool_result(result)
                elif isinstance(event, RealtimeAssistantDone):
                    assistant_done = event
                    break
        except asyncio.CancelledError:
            await session.interrupt()
            raise

        if assistant_done is None:
            raise RuntimeError("realtime session ended without assistant completion")
        report = TurnDriverReport(
            assistant_text=assistant_done.full_text,
            llm_ms=(self._clock.monotonic() - started) * 1000.0,
            usage=usage,
            llm_cost=_legacy_llm_cost(self.pricebook, usage),
            mcp_tools_ms=mcp_tools_ms,
            mcp_tool_calls=mcp_tool_calls,
        )
        for post_hook in self._hooks.post_turn:
            await post_hook(report)
        yield report

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


class GraphTurnDriver:
    def __init__(
        self,
        graph: "CompiledAgentGraph[ConversationState]",
        *,
        session_id: str,
        thread_id: Optional[str] = None,
        clock: Clock,
        state: Optional[ConversationState] = None,
    ) -> None:
        self.graph = graph
        self.session_id = session_id
        self.thread_id = thread_id or session_id
        self.clock = clock
        self.state = state or ConversationState()
        self._history_base_transcript = [
            line.model_copy(deep=True) for line in self.state.transcript
        ]
        self._history_base_turns = self.state.turns
        self._reconciled_turn_id: Optional[str] = None

    @property
    def checkpointer(self) -> Optional[CheckpointStore]:
        return self.graph.checkpointer

    async def run_turn(
        self,
        user_text: str,
        history: Sequence[LlmMessage],
        *,
        turn_context: object | None = None,
    ) -> AsyncIterator[DriverEvent]:
        self.reconcile_history(history)
        turn_id = "turn-%d" % (self.state.turns + 1)
        working_state = self.state.merged(
            {
                "transcript": [
                    *self.state.transcript,
                    TranscriptLine(speaker="caller", text=user_text),
                ],
                "agent_state": {
                    **self.state.agent_state,
                    "current_mcp_tool_calls": 0,
                    "current_rag_requests": 0,
                },
            }
        )
        queue: "asyncio.Queue[TtsSpeak]" = asyncio.Queue()

        def emit(event: object) -> None:
            if isinstance(event, TtsSpeak):
                queue.put_nowait(event)

        outer_context = turn_context if isinstance(turn_context, TurnContext) else None
        ctx = TurnContext(
            payload={"user_text": user_text},
            session_id=self.session_id,
            thread_id=self.thread_id,
            turn_id=turn_id,
            clock=self.clock,
            emit=emit,
            cancellation=(
                outer_context.cancellation
                if outer_context is not None
                else asyncio.Event()
            ),
            speculative=(
                outer_context.speculative if outer_context is not None else False
            ),
            promoted=(
                outer_context.promoted if outer_context is not None else asyncio.Event()
            ),
            current_user_in_state=True,
            rag_dispatch_observer=(
                outer_context.rag_dispatch_observer
                if outer_context is not None
                else None
            ),
            mcp_dispatch_observer=(
                outer_context.mcp_dispatch_observer
                if outer_context is not None
                else None
            ),
        )
        task = asyncio.create_task(self.graph.invoke_turn(working_state, ctx))
        final_state: Optional[ConversationState] = None
        try:
            while True:
                if task.done() and queue.empty():
                    break
                get_task = asyncio.create_task(queue.get())
                done, pending = await asyncio.wait(
                    {task, get_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if get_task in done:
                    yield get_task.result()
                else:
                    get_task.cancel()
                    await asyncio.gather(get_task, return_exceptions=True)
                if task in done:
                    final_state = task.result()
                for item in pending:
                    if item is get_task and not get_task.done():
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

        if final_state is None:
            final_state = task.result()
        if outer_context is not None and outer_context.speculative:
            await outer_context.promoted.wait()
        self.state = final_state
        await self._save_turn_final(turn_id, final_state)
        yield self._report_from_state(final_state)

    def reconcile_history(self, history: Sequence[LlmMessage]) -> None:
        """Adopt the live session transcript, whose assistant text is playback-safe."""
        session_transcript: List[TranscriptLine] = []
        for message in history:
            if message.role == "user":
                session_transcript.append(
                    TranscriptLine(speaker="caller", text=message.content)
                )
            elif message.role == "assistant":
                session_transcript.append(
                    TranscriptLine(speaker="agent", text=message.content)
                )
        if not session_transcript:
            return

        transcript = [
            *(line.model_copy(deep=True) for line in self._history_base_transcript),
            *session_transcript,
        ]
        session_turns = sum(line.speaker == "caller" for line in session_transcript)
        self.state = self.state.merged(
            {
                "transcript": transcript,
                "turns": self._history_base_turns + session_turns,
            }
        )
        self._reconciled_turn_id = "turn-%d" % self.state.turns

    async def persist_reconciled_history(self) -> None:
        checkpointer = self.graph.checkpointer
        turn_id = self._reconciled_turn_id
        if checkpointer is None or turn_id is None:
            return

        history = await checkpointer.history(self.thread_id)
        current_turn = [
            checkpoint
            for checkpoint in history
            if checkpoint.session_id == self.session_id
            and checkpoint.turn_id == turn_id
        ]
        final = next(
            (
                checkpoint
                for checkpoint in reversed(current_turn)
                if checkpoint.kind == "turn_final"
            ),
            None,
        )
        if final is not None and final.state == self.state:
            self._reconciled_turn_id = None
            return
        if final is not None:
            corrected = final.model_copy(
                update={
                    "state": self.state.model_copy(deep=True),
                    "created_at_ms": int(self.clock.monotonic() * 1000),
                },
                deep=True,
            )
        else:
            superstep = (
                max(
                    (checkpoint.superstep for checkpoint in current_turn),
                    default=0,
                )
                + 1
            )
            corrected = Checkpoint(
                checkpoint_id=checkpoint_id(self.session_id, turn_id, superstep),
                session_id=self.session_id,
                thread_id=self.thread_id,
                turn_id=turn_id,
                superstep=superstep,
                kind="turn_final",
                state=self.state,
                created_at_ms=int(self.clock.monotonic() * 1000),
            )
        await checkpointer.save(corrected)
        self._reconciled_turn_id = None

    async def _save_turn_final(self, turn_id: str, state: ConversationState) -> None:
        checkpointer = self.graph.checkpointer
        if checkpointer is None:
            return
        history = await checkpointer.history(self.thread_id)
        last_superstep = max(
            (
                checkpoint.superstep
                for checkpoint in history
                if checkpoint.turn_id == turn_id
            ),
            default=0,
        )
        final_superstep = last_superstep + 1
        await checkpointer.save(
            Checkpoint(
                checkpoint_id=checkpoint_id(self.session_id, turn_id, final_superstep),
                session_id=self.session_id,
                thread_id=self.thread_id,
                turn_id=turn_id,
                superstep=final_superstep,
                kind="turn_final",
                state=state,
                created_at_ms=int(self.clock.monotonic() * 1000),
            )
        )

    def _report_from_state(self, state: ConversationState) -> TurnDriverReport:
        report = state.agent_state.get("last_turn_report", {})
        assistant_text = str(report.get("assistant_text", ""))
        if not assistant_text:
            agent_lines = [
                line.text for line in state.transcript if line.speaker == "agent"
            ]
            assistant_text = agent_lines[-1] if agent_lines else ""
        prompt_tokens = int(report.get("prompt_tokens", 0))
        completion_tokens = int(report.get("completion_tokens", 0))
        cached_prompt_tokens = int(report.get("cached_prompt_tokens", 0))
        usage = (
            UsageReport(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_prompt_tokens=cached_prompt_tokens,
            )
            if prompt_tokens or completion_tokens or cached_prompt_tokens
            else None
        )
        return TurnDriverReport(
            assistant_text=assistant_text,
            llm_ms=float(report.get("llm_ms", 0.0)),
            usage=usage,
            mcp_tools_ms=float(report.get("mcp_tools_ms", 0.0)),
            mcp_tool_calls=int(report.get("mcp_tool_calls", 0))
            + int(state.agent_state.get("current_mcp_tool_calls", 0)),
            rag_requests=int(report.get("rag_requests", 0)),
        )

    @classmethod
    async def resume(
        cls,
        graph: "CompiledAgentGraph[ConversationState]",
        *,
        session_id: str,
        thread_id: Optional[str] = None,
        store: CheckpointStore,
        clock: Clock,
    ) -> "GraphTurnDriver":
        resolved_thread_id = thread_id or session_id
        latest = await store.load_latest(resolved_thread_id)
        state = latest.state if latest is not None else ConversationState()
        return cls(
            graph,
            session_id=session_id,
            thread_id=resolved_thread_id,
            clock=clock,
            state=state,
        )
