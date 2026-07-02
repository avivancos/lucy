"""Deterministic in-process gateway simulator (ADR 0003 no-mocks, ADR 0011).

`LocalGatewaySimulator` is a real implementation of the control-channel schema,
not a mock: it turns a :class:`~lucy.evals.SyntheticCallScenario` into the
upstream event stream a media gateway would produce (caller STT partials/final,
TTS playback in response to the session's ``TtsSpeak``, optional barge-in) and
accepts the session's downstream directives. Time is virtual - envelope
timestamps advance by fixed budget-shaped increments, so no wall clock is ever
consumed and tests are fully deterministic.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, FrozenSet, Iterable, Tuple

from lucy.clock import Clock, MonotonicClock
from lucy.evals import SyntheticCallScenario
from lucy.settings import LatencyBudgets
from lucy.transport.schema import (
    BargeIn,
    ControlEvent,
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    TtsSpeak,
    TtsPlayback,
    VadSpeechStart,
)


class LocalGatewaySimulator:
    """Drives a scenario as a live control channel.

    Only the scenario's ``caller`` turns produce STT; each is answered by the
    session's ``TtsSpeak``, which the gateway echoes back as playback. Caller
    turn indices in ``barge_in_turns`` interrupt the agent mid-utterance.
    """

    def __init__(
        self,
        scenario: SyntheticCallScenario,
        clock: Clock | None = None,
        *,
        session_id: str = "sess_sim",
        provider: str = "local",
        budgets: LatencyBudgets | None = None,
        barge_in_turns: Iterable[int] = (),
        vad_interrupt_turns: Iterable[int] = (),
    ) -> None:
        self.scenario = scenario
        self.clock = clock or MonotonicClock()
        self.session_id = session_id
        self.provider = provider
        self.budgets = budgets or LatencyBudgets()
        # Caller turns that interrupt the agent while it is SPEAKING (BargeIn)
        # vs. while it is still THINKING (VadSpeechStart, before any playback).
        self.barge_in_turns: FrozenSet[int] = frozenset(barge_in_turns)
        self.vad_interrupt_turns: FrozenSet[int] = frozenset(vad_interrupt_turns)
        self._inbound: "asyncio.Queue[Tuple[Envelope, object]]" = asyncio.Queue()
        self._seq = 0
        # Virtual time seeded from the injected clock; it then advances by budget
        # increments per event (no wall-clock sleeping, so tests stay deterministic).
        self._ts_ms = int(self.clock.monotonic() * 1000)

    # -- downstream (session -> gateway) ------------------------------------

    async def send(self, envelope: Envelope, payload: object) -> None:
        await self._inbound.put((envelope, payload))

    # -- upstream (gateway -> session) --------------------------------------

    def _emit(self, type_: str, payload, *, turn_id: str | None = None) -> ControlEvent:
        self._seq += 1
        envelope = Envelope(
            type=type_,
            session_id=self.session_id,
            turn_id=turn_id,
            seq=self._seq,
            ts_ms=self._ts_ms,
        )
        return ControlEvent(envelope, payload)

    async def events(self) -> AsyncIterator[ControlEvent]:
        yield self._emit(
            "session.started",
            SessionStarted(transport="sim", caller="+10000000000", codecs=["pcmu"]),
        )

        caller_turns = [t for t in self.scenario.turns if t.speaker == "caller"]
        for index, turn in enumerate(caller_turns):
            turn_id = "turn_%d" % index
            async for event in self._caller_turn(turn_id, turn.text):
                yield event
            if index in self.vad_interrupt_turns:
                # Caller starts talking again while the agent is still THINKING:
                # no response is spoken this turn (the session cancels it on VAD).
                self._ts_ms += self.budgets.gateway_pacing_ms
                yield self._emit(
                    "vad.speech_start",
                    VadSpeechStart(at_ms=self._ts_ms),
                    turn_id=turn_id,
                )
                continue
            async for event in self._agent_response(turn_id, interrupt=index in self.barge_in_turns):
                yield event

        yield self._emit("session.ended", SessionEnded(reason="scenario_complete"))

    async def _caller_turn(self, turn_id: str, text: str) -> AsyncIterator[ControlEvent]:
        words = text.split()
        accumulated = ""
        for position, word in enumerate(words):
            accumulated = (accumulated + " " + word).strip()
            self._ts_ms += self.budgets.gateway_pacing_ms
            stability = round((position + 1) / len(words), 6)
            yield self._emit(
                "stt.partial",
                SttPartial(text=accumulated, stability=stability, provider=self.provider),
                turn_id=turn_id,
            )
        self._ts_ms += self.budgets.stt_final_ms
        yield self._emit(
            "stt.final",
            SttFinal(text=text, provider=self.provider, stt_ms=self.budgets.stt_final_ms),
            turn_id=turn_id,
        )

    async def _agent_response(
        self, turn_id: str, *, interrupt: bool
    ) -> AsyncIterator[ControlEvent]:
        # Wait for the session's TtsSpeak (queued by send() before it asks for
        # the next event, so this resolves without deadlock).
        _, directive = await self._inbound.get()
        if not isinstance(directive, TtsSpeak):
            return
        utterance = directive.utterance_id
        full = len(directive.text)

        self._ts_ms += self.budgets.gateway_pacing_ms
        yield self._emit(
            "tts.playback",
            TtsPlayback(utterance_id=utterance, state="started", mark_chars=0),
            turn_id=turn_id,
        )
        if interrupt:
            heard = max(1, full // 2)
            self._ts_ms += self.budgets.gateway_pacing_ms
            yield self._emit(
                "tts.playback",
                TtsPlayback(utterance_id=utterance, state="mark", mark_chars=heard),
                turn_id=turn_id,
            )
            self._ts_ms += self.budgets.gateway_pacing_ms
            yield self._emit(
                "barge_in",
                BargeIn(at_ms=self._ts_ms, during="speaking", utterance_id=utterance),
                turn_id=turn_id,
            )
            return
        self._ts_ms += self.budgets.gateway_pacing_ms
        yield self._emit(
            "tts.playback",
            TtsPlayback(utterance_id=utterance, state="mark", mark_chars=full),
            turn_id=turn_id,
        )
        self._ts_ms += self.budgets.gateway_pacing_ms
        yield self._emit(
            "tts.playback",
            TtsPlayback(utterance_id=utterance, state="finished", mark_chars=full),
            turn_id=turn_id,
        )
