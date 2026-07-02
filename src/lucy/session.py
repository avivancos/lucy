"""The VoiceSession actor (ADR 0011): the smallest end-to-end live turn loop.

A `VoiceSession` consumes control-channel events from a transport, drives the
per-turn state machine (IDLE -> LISTENING -> THINKING -> SPEAKING -> IDLE), runs
each turn as one cancellable asyncio task, cancels it on either a ``BargeIn``
(caller interrupts the agent's speech) or a ``VadSpeechStart`` (caller starts
talking again) during THINKING/SPEAKING - truncating the spoken text to what
the caller actually heard - and finalizes a real :class:`LatencyWaterfall` per
turn. In M0 the ``responder`` is a plain callable returning canned text; the
streaming LLM driver replaces it in card 33.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, List, Optional

from lucy.clock import Clock, MonotonicClock
from lucy.metrics import CostBreakdown, LatencyWaterfall
from lucy.settings import LatencyBudgets
from lucy.tracing import Span, TurnSpanTree
from lucy.transport.schema import (
    BargeIn,
    ControlEvent,
    Envelope,
    SessionEnded,
    SttFinal,
    SttPartial,
    TtsPlayback,
    TtsSpeak,
    VadSpeechStart,
)

Responder = Callable[[str], Awaitable[str]]


class TurnState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass
class TurnRecord:
    turn_id: str
    user_text: str
    assistant_text: str
    interrupted: bool
    waterfall: LatencyWaterfall
    cost: Optional[CostBreakdown] = None


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
    task: "Optional[asyncio.Task[None]]" = None
    playback_finished: asyncio.Event = field(default_factory=asyncio.Event)


class VoiceSession:
    def __init__(
        self,
        session_id: str,
        transport,
        responder: Responder,
        *,
        state_store=None,
        tracer=None,
        clock: Optional[Clock] = None,
        budgets: Optional[LatencyBudgets] = None,
    ) -> None:
        self.session_id = session_id
        self.transport = transport
        self.responder = responder
        self.state_store = state_store
        self.tracer = tracer
        self.clock = clock or MonotonicClock()
        self.budgets = budgets or LatencyBudgets()
        self.state = TurnState.IDLE
        self.transitions: List[TurnState] = [TurnState.IDLE]
        self.spans: List[Span] = []

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

        async for event in self.transport.events():
            payload = event.payload
            last_ts = event.envelope.ts_ms
            if not started:
                tree.start_session(event.envelope.ts_ms)
                started = True

            if isinstance(payload, SttPartial):
                self._set_state(TurnState.LISTENING)

            elif isinstance(payload, SttFinal):
                ts = event.envelope.ts_ms
                active = _ActiveTurn(
                    turn_id=event.envelope.turn_id or "turn",
                    user_text=payload.text,
                    stt_ms=payload.stt_ms,
                    stt_final_ms=ts,
                    started_ms=max(0, ts - payload.stt_ms),
                )
                self._set_state(TurnState.THINKING)
                active.task = asyncio.create_task(self._run_turn(active))

            elif isinstance(payload, TtsPlayback) and active is not None:
                active.mark_chars = max(active.mark_chars, payload.mark_chars)
                if payload.state == "started":
                    active.speak_started_ms = event.envelope.ts_ms
                    self._set_state(TurnState.SPEAKING)
                elif payload.state == "finished":
                    active.ended_ms = event.envelope.ts_ms
                    active.tts_ms = event.envelope.ts_ms - active.speak_started_ms
                    active.playback_finished.set()
                    await self._await_task(active)
                    records.append(self._finalize(active, tree))
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
            records.append(self._finalize(active, tree))

        tree.end_session(last_ts)
        tree.emit(self.tracer)
        self.spans = list(tree.spans)
        return records

    async def _run_turn(self, turn: _ActiveTurn) -> None:
        llm_start = self.clock.monotonic()
        turn.assistant_text = await self.responder(turn.user_text)
        turn.llm_ms = (self.clock.monotonic() - llm_start) * 1000
        await self.transport.send(
            Envelope(
                type="tts.speak",
                session_id=self.session_id,
                turn_id=turn.turn_id,
                seq=0,
                ts_ms=0,
            ),
            TtsSpeak(
                utterance_id="utt_%s" % turn.turn_id,
                text=turn.assistant_text,
                flush=False,
            ),
        )
        await turn.playback_finished.wait()

    async def _interrupt(
        self,
        turn: _ActiveTurn,
        ts_ms: int,
        tree: TurnSpanTree,
        records: List[TurnRecord],
    ) -> None:
        turn.interrupted = True
        turn.ended_ms = ts_ms
        # Only a turn that actually began SPEAKING has a TTS slice; interrupting
        # during THINKING leaves speak_started_ms at 0, so guard the subtraction
        # (an absolute timestamp here would corrupt the waterfall).
        if turn.speak_started_ms:
            turn.tts_ms = max(turn.tts_ms, ts_ms - turn.speak_started_ms)
        await self._cancel_task(turn)
        records.append(self._finalize(turn, tree))
        self._set_state(TurnState.IDLE)

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

    def _finalize(self, turn: _ActiveTurn, tree: TurnSpanTree) -> TurnRecord:
        assistant_text = turn.assistant_text
        if turn.interrupted:
            assistant_text = assistant_text[: turn.mark_chars]
        waterfall = LatencyWaterfall(
            stt_ms=turn.stt_ms,
            rag_ms=0.0,
            llm_ms=turn.llm_ms,
            mcp_tools_ms=0.0,
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
            cost=None,
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
            tree.node_span(
                turn_span, "tts", turn.speak_started_ms, end, status=status
            )
