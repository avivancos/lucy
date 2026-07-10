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
    AmdResult,
    BargeIn,
    ControlEvent,
    Dtmf,
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    TtsSpeak,
    TtsCancel,
    TtsPlayback,
    TtsStreamEnd,
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
        dtmf_steps: Iterable[str] = (),
        amd_steps: Iterable[AmdResult] = (),
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
        self.dtmf_steps = tuple(dtmf_steps)
        self.amd_steps = tuple(amd_steps)
        self._inbound: "asyncio.Queue[Tuple[Envelope, object]]" = asyncio.Queue()
        self.sent: list[ControlEvent] = []
        self.directives: list[object] = []
        self._seq = 0
        # Virtual time seeded from the injected clock; it then advances by budget
        # increments per event (no wall-clock sleeping, so tests stay deterministic).
        self._ts_ms = int(self.clock.monotonic() * 1000)

    # -- downstream (session -> gateway) ------------------------------------

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.sent.append(ControlEvent(envelope, payload))
        self.directives.append(payload)
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
        for digit in self.dtmf_steps:
            yield self._emit("dtmf", Dtmf(digit=digit))
        for result in self.amd_steps:
            yield self._emit("amd.result", result)

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
                # Drain the cancelled turn's directives up to its stream-end
                # barrier so nothing leaks into the next turn (card 64). The
                # session guarantees the barrier even for a cancelled turn.
                await self._collect_turn_directives(turn_id)
                continue
            async for event in self._agent_response(
                turn_id, interrupt=index in self.barge_in_turns
            ):
                yield event

        yield self._emit("session.ended", SessionEnded(reason="scenario_complete"))

    async def _caller_turn(
        self, turn_id: str, text: str
    ) -> AsyncIterator[ControlEvent]:
        words = text.split()
        accumulated = ""
        for position, word in enumerate(words):
            accumulated = (accumulated + " " + word).strip()
            self._ts_ms += self.budgets.gateway_pacing_ms
            stability = round((position + 1) / len(words), 6)
            yield self._emit(
                "stt.partial",
                SttPartial(
                    text=accumulated, stability=stability, provider=self.provider
                ),
                turn_id=turn_id,
            )
        self._ts_ms += self.budgets.stt_final_ms
        yield self._emit(
            "stt.final",
            SttFinal(
                text=text, provider=self.provider, stt_ms=self.budgets.stt_final_ms
            ),
            turn_id=turn_id,
        )

    async def _collect_turn_directives(self, turn_id: str) -> "list[TtsSpeak]":
        """Drain the turn's downstream directives up to the ``TtsStreamEnd``
        barrier the session sends exactly once per turn (card 64). Collecting
        the whole utterance set first is what lets a multi-clause turn play
        back deterministically and never leak clauses into the next turn."""
        utterances: "list[TtsSpeak]" = []
        while True:
            envelope, directive = await self._inbound.get()
            if envelope.turn_id not in (None, turn_id):
                continue
            if isinstance(directive, TtsSpeak):
                utterances.append(directive)
            elif isinstance(directive, TtsStreamEnd):
                return utterances
            # other directives (e.g. future tts.cancel) don't end collection

    def _playback(
        self, turn_id: str, utterance_id: str, state: str, mark_chars: int
    ) -> ControlEvent:
        self._ts_ms += self.budgets.gateway_pacing_ms
        return self._emit(
            "tts.playback",
            TtsPlayback(utterance_id=utterance_id, state=state, mark_chars=mark_chars),
            turn_id=turn_id,
        )

    async def _agent_response(
        self, turn_id: str, *, interrupt: bool
    ) -> AsyncIterator[ControlEvent]:
        # Collect every clause of the turn (the session's stream-end barrier
        # bounds the wait, so this resolves without deadlock).
        utterances = await self._collect_turn_directives(turn_id)
        if not utterances:
            return

        # started for the first clause only; the session's SPEAKING transition
        # and speak_started_ms key off this single event.
        yield self._playback(turn_id, utterances[0].utterance_id, "started", 0)

        if interrupt:
            # Barge-in cuts the LAST clause mid-utterance: earlier clauses were
            # fully heard (finished), the last is truncated at its mark.
            for utt in utterances[:-1]:
                yield self._playback(turn_id, utt.utterance_id, "mark", len(utt.text))
                yield self._playback(
                    turn_id, utt.utterance_id, "finished", len(utt.text)
                )
            last = utterances[-1]
            heard = max(1, len(last.text) // 2)
            yield self._playback(turn_id, last.utterance_id, "mark", heard)
            self._ts_ms += self.budgets.gateway_pacing_ms
            yield self._emit(
                "barge_in",
                BargeIn(
                    at_ms=self._ts_ms,
                    during="speaking",
                    utterance_id=last.utterance_id,
                ),
                turn_id=turn_id,
            )
            async for event in self._flush_after_cancel(turn_id, last, heard):
                yield event
            return

        # Clean playback: a mark per clause, one terminal finished after the
        # last (the session completes the turn on the last utterance only).
        for utt in utterances:
            yield self._playback(turn_id, utt.utterance_id, "mark", len(utt.text))
        last = utterances[-1]
        yield self._playback(turn_id, last.utterance_id, "finished", len(last.text))

    async def _flush_after_cancel(
        self, turn_id: str, utterance: TtsSpeak, heard: int
    ) -> AsyncIterator[ControlEvent]:
        while True:
            try:
                envelope, directive = self._inbound.get_nowait()
            except asyncio.QueueEmpty:
                return
            if envelope.turn_id not in (None, turn_id):
                continue
            if isinstance(directive, TtsCancel):
                yield self._playback(turn_id, utterance.utterance_id, "flushed", heard)
                return
            elif isinstance(directive, TtsStreamEnd):
                return
