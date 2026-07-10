"""The VoiceSession actor (ADR 0011): the smallest end-to-end live turn loop.

A `VoiceSession` consumes control-channel events from a transport, drives the
per-turn state machine (IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE), runs
each turn as one cancellable asyncio task, cancels it on either a ``BargeIn``
(caller interrupts the agent's speech) or a ``VadSpeechStart`` (caller starts
talking again) during THINKING/SPEAKING - truncating the spoken text to what
the caller actually heard - and finalizes a real :class:`LatencyWaterfall` per
turn. Exactly one response path is wired: a ``responder`` (a plain callable
returning canned text, M0) or a streaming-LLM ``driver`` (card 33) - never both.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, List, Optional, Sequence

from lucy.clock import Clock, MonotonicClock
from lucy.drivers import TurnDriver, TurnDriverReport
from lucy.llm import LlmMessage, compute_cache_key
from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.rag import RagResult, SpeculativeRagNode, grounded_context_message
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets, SpeculationSettings
from lucy.speech import TtsPlanner
from lucy.tracing import Span, TurnSpanTree
from lucy.transport.schema import (
    BargeIn,
    Envelope,
    SessionEnded,
    SttFinal,
    SttPartial,
    TtsCancel,
    TtsPlayback,
    TtsSpeak,
    TtsStreamEnd,
    VadSpeechStart,
)

Responder = Callable[[str], Awaitable[str]]

# Floor so a clock-measured turn duration always satisfies CostBreakdown's
# billable_audio_minutes > 0 constraint (not a budget - a numerical guard).
_MIN_BILLABLE_MINUTES = 1e-9


def heard_assistant_text(
    utterance_texts: Sequence[tuple[str, str]],
    playbacks: Sequence[TtsPlayback],
) -> str:
    """Reconstruct exactly what the caller heard from playback marks."""
    by_utterance: dict[str, list[TtsPlayback]] = {}
    for playback in playbacks:
        by_utterance.setdefault(playback.utterance_id, []).append(playback)

    parts: list[str] = []
    for utterance_id, text in utterance_texts:
        events = by_utterance.get(utterance_id, [])
        if not events:
            break
        if events[-1].state == "finished":
            parts.append(text)
            continue
        flushed = [event for event in events if event.state == "flushed"]
        marks = [event for event in events if event.state == "mark"]
        if flushed:
            heard_chars = flushed[-1].mark_chars
        elif marks:
            heard_chars = marks[-1].mark_chars
        else:
            heard_chars = 0
        heard = text[: max(0, min(heard_chars, len(text)))]
        if heard:
            parts.append(heard)
        break
    return " ".join(parts)


class TurnState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class SpeculativeAction(str, Enum):
    NONE = "none"
    PREFETCH_RAG = "prefetch_rag"
    START_LLM = "start_llm"


def _normalize_speculative_text(text: str) -> str:
    return " ".join(text.split()).casefold()


class SpeculationController:
    def __init__(self, settings: SpeculationSettings) -> None:
        self.settings = settings
        self._prefetched: set[str] = set()
        self._trigger_text = ""
        self._task: Optional[asyncio.Task] = None
        self._llm_started = False

    @property
    def speculating(self) -> bool:
        return self._task is not None and not self._task.done()

    def on_partial(self, text: str, stability: float) -> SpeculativeAction:
        normalized = _normalize_speculative_text(text)
        if (
            self.settings.enabled_llm_start
            and stability >= self.settings.llm_start_stability
            and not self._llm_started
        ):
            self._llm_started = True
            return SpeculativeAction.START_LLM
        if (
            self.settings.enabled_rag_prefetch
            and stability >= self.settings.rag_prefetch_stability
            and normalized not in self._prefetched
        ):
            self._prefetched.add(normalized)
            return SpeculativeAction.PREFETCH_RAG
        return SpeculativeAction.NONE

    def start(self, text: str, task: asyncio.Task) -> None:
        self._trigger_text = text
        self._task = task

    async def reconcile(self, final: str) -> bool:
        task = self._task
        trigger = _normalize_speculative_text(self._trigger_text)
        final_text = _normalize_speculative_text(final)
        promoted = bool(task) and bool(trigger) and final_text.startswith(trigger)
        if task is not None and not promoted:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)
        self._prefetched.clear()
        self._trigger_text = ""
        self._task = None
        self._llm_started = False
        return promoted


@dataclass
class TurnRecord:
    turn_id: str
    user_text: str
    assistant_text: str
    interrupted: bool
    waterfall: LatencyWaterfall
    cost: Optional[CostBreakdown] = None
    rag_cache_hit: bool = False


@dataclass
class _ActiveTurn:
    turn_id: str
    user_text: str
    stt_ms: int
    started_ms: int = 0
    stt_final_ms: int = 0
    speak_started_ms: int = 0
    ended_ms: int = 0
    assistant_text: str = ""
    interrupted: bool = False
    mark_chars: int = 0
    tts_ms: int = 0
    llm_ms: float = 0.0
    llm_cost: float = 0.0
    mcp_tools_ms: float = 0.0
    rag_ms: float = 0.0
    rag_cache_hit: bool = False
    rag_result: Optional[RagResult] = None
    clock_start: float = 0.0
    barrier_sent: bool = False  # tts.stream_end sent exactly once per turn
    planner: Optional[TtsPlanner] = None  # set in driver mode
    task: "Optional[asyncio.Task[None]]" = None
    playback_finished: asyncio.Event = field(default_factory=asyncio.Event)
    utterance_texts: List[tuple[str, str]] = field(default_factory=list)
    playbacks: List[TtsPlayback] = field(default_factory=list)
    background_tasks: "set[asyncio.Task]" = field(default_factory=set)
    record: Optional[TurnRecord] = None


class VoiceSession:
    def __init__(
        self,
        session_id: str,
        transport,
        responder: Optional[Responder] = None,
        *,
        driver: Optional[TurnDriver] = None,
        state_store=None,
        tracer=None,
        clock: Optional[Clock] = None,
        budgets: Optional[LatencyBudgets] = None,
        speculation: Optional[SpeculationSettings] = None,
        rag: Optional[SpeculativeRagNode] = None,
    ) -> None:
        if (responder is None) == (driver is None):
            raise ValueError("exactly one of responder or driver must be set")
        self.session_id = session_id
        self.transport = transport
        self.responder = responder
        self.driver = driver
        self.state_store = state_store
        self.tracer = tracer
        self.clock = clock or MonotonicClock()
        self.budgets = budgets or LatencyBudgets()
        self.speculation = speculation or SpeculationSettings()
        self.rag = rag
        self.state = TurnState.IDLE
        self.transitions: List[TurnState] = [TurnState.IDLE]
        self.spans: List[Span] = []
        self._history: List[LlmMessage] = []
        self._background_tasks: "set[asyncio.Task]" = set()
        self._prefetch_tasks: "set[asyncio.Task]" = set()
        self._cache_key = compute_cache_key(session_id, "")
        cache_key_setter = getattr(self.driver, "set_cache_key", None)
        if cache_key_setter is not None:
            cache_key_setter(self._cache_key)

    def _set_state(self, state: TurnState) -> None:
        if state != self.state:
            self.state = state
            self.transitions.append(state)

    async def run(self) -> List[TurnRecord]:
        records: List[TurnRecord] = []
        active: Optional[_ActiveTurn] = None
        tree = TurnSpanTree(self.session_id)
        started = False
        last_ts = 0
        controller = SpeculationController(self.speculation)
        speculative_turn: Optional[_ActiveTurn] = None
        speculative_context: Optional[TurnContext] = None

        async for event in self.transport.events():
            payload = event.payload
            last_ts = event.envelope.ts_ms
            if not started:
                tree.start_session(event.envelope.ts_ms)
                started = True

            if isinstance(payload, SttPartial):
                self._set_state(TurnState.LISTENING)
                action = controller.on_partial(payload.text, payload.stability)
                if action == SpeculativeAction.PREFETCH_RAG and self.rag is not None:
                    self._track_prefetch(
                        asyncio.create_task(self.rag.prefetch(payload.text))
                    )
                elif (
                    action == SpeculativeAction.START_LLM
                    and self.driver is not None
                    and speculative_turn is None
                ):
                    speculative_turn = _ActiveTurn(
                        turn_id=event.envelope.turn_id or "turn",
                        user_text=payload.text,
                        stt_ms=0,
                        stt_final_ms=event.envelope.ts_ms,
                        started_ms=event.envelope.ts_ms,
                        clock_start=self.clock.monotonic(),
                    )
                    speculative_context = TurnContext(
                        payload={"user_text": payload.text},
                        session_id=self.session_id,
                        turn_id=speculative_turn.turn_id,
                        clock=self.clock,
                        speculative=True,
                    )
                    speculative_turn.task = asyncio.create_task(
                        self._run_driver_turn(speculative_turn, speculative_context)
                    )
                    controller.start(payload.text, speculative_turn.task)

            elif isinstance(payload, SttFinal):
                if active is not None:
                    # A turn with no playback at all (e.g. a zero-clause driver
                    # reply) never sees a terminal finished event: reap and
                    # record it before starting the next turn - a turn is never
                    # dropped and its task never leaks (card 64).
                    active.ended_ms = active.ended_ms or event.envelope.ts_ms
                    await self._cancel_task(active)
                    await self._complete(active, tree, records)
                promoted = await controller.reconcile(payload.text)
                if promoted and speculative_turn is not None:
                    active = speculative_turn
                    active.user_text = payload.text
                    active.stt_ms = payload.stt_ms
                    active.stt_final_ms = event.envelope.ts_ms
                    active.started_ms = max(0, event.envelope.ts_ms - payload.stt_ms)
                    if speculative_context is not None:
                        speculative_context.speculative = False
                        speculative_context.promoted.set()
                        for directive in speculative_context.buffered_directives:
                            await self._send_tts_speak(
                                active, directive, speculative_context
                            )
                        speculative_context.buffered_directives.clear()
                    await self._retrieve_rag(active)
                    if active.task is not None and active.task.done():
                        await self._await_task(active)
                        await self._send_stream_end(active)
                    self._set_state(TurnState.THINKING)
                    speculative_turn = None
                    speculative_context = None
                    continue

                speculative_turn = None
                speculative_context = None
                ts = event.envelope.ts_ms
                active = _ActiveTurn(
                    turn_id=event.envelope.turn_id or "turn",
                    user_text=payload.text,
                    stt_ms=payload.stt_ms,
                    stt_final_ms=ts,
                    started_ms=max(0, ts - payload.stt_ms),
                    clock_start=self.clock.monotonic(),
                )
                self._set_state(TurnState.THINKING)
                turn_body = self._run_driver_turn if self.driver else self._run_turn
                active.task = asyncio.create_task(turn_body(active))

            elif isinstance(payload, TtsPlayback) and active is not None:
                active.playbacks.append(payload)
                active.mark_chars = max(active.mark_chars, payload.mark_chars)
                if active.planner is not None:
                    active.planner.record_playback(payload)
                if payload.state == "started":
                    active.speak_started_ms = event.envelope.ts_ms
                    self._set_state(TurnState.SPEAKING)
                elif payload.state == "finished":
                    # Multi-clause turns (card 64): only the LAST registered
                    # utterance's finished playback ends the turn; earlier
                    # clauses' finishes are recorded (above) but not terminal.
                    if active.planner is None or active.planner.is_last(
                        payload.utterance_id
                    ):
                        active.ended_ms = event.envelope.ts_ms
                        active.tts_ms = event.envelope.ts_ms - active.speak_started_ms
                        active.playback_finished.set()
                        await self._await_task(active)
                        await self._complete(active, tree, records)
                        self._set_state(TurnState.IDLE)
                        active = None

            elif isinstance(payload, (BargeIn, VadSpeechStart)) and active is not None:
                # Both interrupt an in-flight turn: BargeIn = caller talks over the
                # agent's speech; VadSpeechStart = caller starts a new utterance
                # while the agent is still thinking/speaking (ADR 0011 spec).
                if self.state in (TurnState.THINKING, TurnState.SPEAKING):
                    await self._interrupt(active, event.envelope.ts_ms, tree, records)
                    active = None

            elif isinstance(payload, SessionEnded):
                break

        # A turn still in flight when the channel closed: cancel and record it.
        if active is not None:
            active.ended_ms = active.ended_ms or last_ts
            await self._cancel_task(active)
            await self._complete(active, tree, records)

        if self._prefetch_tasks:
            await asyncio.gather(*list(self._prefetch_tasks), return_exceptions=True)
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

        tree.end_session(last_ts)
        tree.emit(self.tracer)
        self.spans = list(tree.spans)
        return records

    async def _send_stream_end(self, turn: _ActiveTurn) -> None:
        """End-of-speech barrier: exactly once per turn, on every path (normal
        completion or cancellation), so the gateway simulator can drain the
        turn's directives deterministically (card 64)."""
        if turn.barrier_sent:
            return
        turn.barrier_sent = True
        await self.transport.send(
            Envelope(
                type="tts.stream_end",
                session_id=self.session_id,
                turn_id=turn.turn_id,
                seq=0,
                ts_ms=0,
            ),
            TtsStreamEnd(),
        )

    async def _run_turn(self, turn: _ActiveTurn) -> None:
        assert self.responder is not None
        try:
            await self._retrieve_rag(turn)
            llm_start = self.clock.monotonic()
            turn.assistant_text = await self.responder(turn.user_text)
            turn.llm_ms = (self.clock.monotonic() - llm_start) * 1000
            utterance = TtsSpeak(
                utterance_id="utt_%s" % turn.turn_id,
                text=turn.assistant_text,
                flush=False,
            )
            turn.utterance_texts.append((utterance.utterance_id, utterance.text))
            await self._send_tts_speak(turn, utterance)
        finally:
            await self._send_stream_end(turn)
        await turn.playback_finished.wait()

    async def _run_driver_turn(
        self, turn: _ActiveTurn, context: TurnContext | None = None
    ) -> None:
        assert self.driver is not None
        turn.planner = TtsPlanner()
        adopter = getattr(self.driver, "set_background_task_adopter", None)
        if adopter is not None:
            adopter(lambda task: self._adopt_background_task(turn, task))
        first_tts = True
        try:
            await self._retrieve_rag(turn)
            history = list(self._history)
            if turn.rag_result is not None:
                context_message = grounded_context_message(turn.rag_result)
                if context_message is not None:
                    history.insert(0, context_message)
            async for event in self.driver.run_turn(
                turn.user_text, history, turn_context=context
            ):
                if isinstance(event, TtsSpeak):
                    turn.utterance_texts.append((event.utterance_id, event.text))
                    turn.planner.register(event.utterance_id, event.text)
                    if first_tts:
                        self._set_state(TurnState.SPEAKING)  # THINKING -> SPEAKING
                        first_tts = False
                    await self._send_tts_speak(turn, event, context)
                elif isinstance(event, TurnDriverReport):
                    turn.assistant_text = event.assistant_text
                    turn.llm_ms = event.llm_ms
                    turn.llm_cost = event.llm_cost
                    turn.mcp_tools_ms = event.mcp_tools_ms
        finally:
            if context is None or not context.speculative:
                await self._send_stream_end(turn)
        if context is None or not context.speculative:
            await turn.playback_finished.wait()

    async def _send_tts_speak(
        self,
        turn: _ActiveTurn,
        utterance: TtsSpeak,
        context: TurnContext | None = None,
    ) -> None:
        if context is not None and context.speculative:
            context.buffered_directives.append(utterance)
            return
        await self.transport.send(
            Envelope(
                type="tts.speak",
                session_id=self.session_id,
                turn_id=turn.turn_id,
                seq=0,
                ts_ms=0,
            ),
            utterance,
        )

    async def _retrieve_rag(self, turn: _ActiveTurn) -> None:
        if self.rag is None:
            return
        started = self.clock.monotonic()
        result = await self.rag.prefetch(turn.user_text)
        turn.rag_ms = (self.clock.monotonic() - started) * 1000.0
        turn.rag_cache_hit = result.cache_hit
        turn.rag_result = result

    async def _interrupt(
        self,
        turn: _ActiveTurn,
        ts_ms: int,
        tree: TurnSpanTree,
        records: List[TurnRecord],
    ) -> None:
        turn.interrupted = True
        turn.ended_ms = ts_ms
        if self.state == TurnState.SPEAKING:
            await self.transport.send(
                Envelope(
                    type="tts.cancel",
                    session_id=self.session_id,
                    turn_id=turn.turn_id,
                    seq=0,
                    ts_ms=0,
                ),
                TtsCancel(utterance_id="all"),
            )
        # Only a turn that actually began SPEAKING has a TTS slice; interrupting
        # during THINKING leaves speak_started_ms at 0, so guard the subtraction
        # (an absolute timestamp here would corrupt the waterfall).
        if turn.speak_started_ms:
            turn.tts_ms = max(turn.tts_ms, ts_ms - turn.speak_started_ms)
        await self._cancel_task(turn)
        # A task cancelled before its first run never executes its finally, so
        # its stream-end barrier was never sent - send it here (exactly-once is
        # guarded by turn.barrier_sent) or the gateway's drain would deadlock.
        await self._send_stream_end(turn)
        await self._complete(turn, tree, records)
        self._set_state(TurnState.LISTENING)

    async def _complete(
        self, turn: _ActiveTurn, tree: TurnSpanTree, records: List[TurnRecord]
    ) -> None:
        record = self._finalize(turn, tree)
        turn.record = record
        records.append(record)
        self._history.append(LlmMessage(role="user", content=record.user_text))
        self._history.append(
            LlmMessage(role="assistant", content=record.assistant_text)
        )
        reconcile_history = getattr(self.driver, "reconcile_history", None)
        if callable(reconcile_history):
            reconcile_history(tuple(self._history))
        persist_history = getattr(self.driver, "persist_reconciled_history", None)
        if callable(persist_history):
            await persist_history()

    async def _await_task(self, turn: _ActiveTurn) -> None:
        if turn.task is not None:
            await turn.task

    async def _cancel_task(self, turn: _ActiveTurn) -> None:
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()
        if turn.task is not None:
            try:
                await turn.task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)

    def _adopt_background_task(self, turn: _ActiveTurn, task: asyncio.Task) -> None:
        turn.background_tasks.add(task)
        self._background_tasks.add(task)

        def _finish(done: asyncio.Task) -> None:
            turn.background_tasks.discard(done)
            self._background_tasks.discard(done)
            try:
                result = done.result()
            except (asyncio.CancelledError, Exception):
                return
            elapsed_ms = getattr(result, "elapsed_ms", 0.0)
            turn.mcp_tools_ms += elapsed_ms
            if turn.record is not None:
                turn.record.waterfall.mcp_tools_ms += elapsed_ms

        task.add_done_callback(_finish)

    def _track_prefetch(self, task: asyncio.Task) -> None:
        self._prefetch_tasks.add(task)

        def _discard(done: asyncio.Task) -> None:
            self._prefetch_tasks.discard(done)
            try:
                done.result()
            except (asyncio.CancelledError, Exception):
                return

        task.add_done_callback(_discard)

    def _finalize(self, turn: _ActiveTurn, tree: TurnSpanTree) -> TurnRecord:
        if turn.planner is not None:
            assistant_text = (
                heard_assistant_text(turn.utterance_texts, turn.playbacks)
                if turn.interrupted
                else turn.assistant_text
            )
            duration_min = (self.clock.monotonic() - turn.clock_start) / 60.0
            cost: Optional[CostBreakdown] = CostBreakdown(
                llm_cost=turn.llm_cost,
                billable_audio_minutes=max(duration_min, _MIN_BILLABLE_MINUTES),
            )
        else:
            assistant_text = (
                heard_assistant_text(turn.utterance_texts, turn.playbacks)
                if turn.interrupted
                else turn.assistant_text
            )
            cost = None
        waterfall = LatencyWaterfall(
            stt_ms=turn.stt_ms,
            rag_ms=turn.rag_ms,
            llm_ms=turn.llm_ms,
            mcp_tools_ms=turn.mcp_tools_ms,
            tts_ms=float(turn.tts_ms),
            transport_ms=float(self.budgets.control_transport_ms),
        )
        self._record_spans(turn, tree)
        return TurnRecord(
            turn_id=turn.turn_id,
            user_text=turn.user_text,
            assistant_text=assistant_text,
            interrupted=turn.interrupted,
            waterfall=waterfall,
            cost=cost,
            rag_cache_hit=turn.rag_cache_hit,
        )

    def _record_spans(self, turn: _ActiveTurn, tree: TurnSpanTree) -> None:
        status = "cancelled" if turn.interrupted else "ok"
        end = turn.ended_ms or turn.stt_final_ms
        turn_span = tree.turn_span(turn.turn_id, turn.started_ms, end, status=status)
        tree.node_span(turn_span, "stt", turn.started_ms, turn.stt_final_ms)
        llm_end = turn.speak_started_ms or end
        tree.node_span(
            turn_span,
            "llm",
            turn.stt_final_ms,
            llm_end,
            attributes={"llm_ms": "%.3f" % turn.llm_ms},
        )
        if turn.speak_started_ms:
            tree.node_span(turn_span, "tts", turn.speak_started_ms, end, status=status)
