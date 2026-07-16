"""Deterministic in-process gateway simulator (ADR 0003 no-mocks, ADR 0011).

`LocalGatewaySimulator` is a real implementation of the control-channel schema,
not a mock: it turns a :class:`~lucy.evals.SyntheticCallScenario` into the
upstream event stream a media gateway would produce (caller STT partials/final,
TTS playback in response to the session's ``TtsSpeak``, optional barge-in) and
accepts the session's downstream directives. Scenario and event pacing is
virtual: envelope timestamps advance by fixed budget-shaped increments.
Recording upload tests intentionally use real local HTTP with bounded timeouts.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import io
import logging
import re
import struct
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Callable, FrozenSet, Iterable, Optional, Tuple
from urllib.parse import urlparse

import httpx

from lucy.clock import Clock, MonotonicClock
from lucy.evals import SyntheticCallScenario
from lucy.limits import MAX_CONTROL_DURATION_MS
from lucy.recording import RecordingUploadTarget
from lucy.settings import LatencyBudgets
from lucy.transport.schema import (
    AmdResult,
    BargeIn,
    ControlEvent,
    Dtmf,
    Envelope,
    SessionEnd,
    SessionEnded,
    SessionStarted,
    RecordingFailed,
    RecordingStart,
    RecordingStarted,
    RecordingUploaded,
    SttFinal,
    SttPartial,
    TtsSpeak,
    TtsCancel,
    TtsPlayback,
    TtsStreamEnd,
    VadSpeechStart,
    VadSpeechEnd,
)

SIMULATOR_RECORDING_SAMPLE_RATE_HZ = 8000
SIMULATOR_RECORDING_DURATION_MS = 100
SIMULATOR_RECORDING_FRAMES = (
    SIMULATOR_RECORDING_SAMPLE_RATE_HZ * SIMULATOR_RECORDING_DURATION_MS // 1000
)
SIMULATOR_RECORDING_AMPLITUDES = {"caller": 1000, "agent": 2000, "mixed": 1500}
SIMULATOR_UPLOAD_TIMEOUT_SECONDS = 5.0
SIMULATOR_RECORDING_CONTAINER = "wav"
_UPLOAD_URL_IN_LOG = re.compile(r"https?://[^\s\"'<>]+")


@dataclass(frozen=True)
class SimulatedSpeakerActivity:
    """Measured media-plane speech intervals for one simulator turn."""

    caller_talk_ms: int
    agent_talk_ms: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("caller_talk_ms", self.caller_talk_ms),
            ("agent_talk_ms", self.agent_talk_ms),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_CONTROL_DURATION_MS
            ):
                raise ValueError(
                    f"{field_name} must be a strict integer in "
                    f"0..{MAX_CONTROL_DURATION_MS}; got {type(value).__name__}"
                )


class _SignedUploadLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _UPLOAD_URL_IN_LOG.sub("<redacted-upload-url>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


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
        speaker_activity: Iterable[SimulatedSpeakerActivity] = (),
        recording_upload_resolver: Optional[
            Callable[[str], RecordingUploadTarget]
        ] = None,
        recording_upload_timeout_seconds: float = SIMULATOR_UPLOAD_TIMEOUT_SECONDS,
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
        self.speaker_activity = tuple(speaker_activity)
        caller_turn_count = sum(
            turn.speaker == "caller" for turn in self.scenario.turns
        )
        if self.speaker_activity and len(self.speaker_activity) != caller_turn_count:
            raise ValueError(
                "speaker activity must contain one profile per caller turn"
            )
        if self.speaker_activity and (self.barge_in_turns or self.vad_interrupt_turns):
            raise ValueError(
                "explicit speaker activity cannot be combined with interruptions"
            )
        self.recording_upload_resolver = recording_upload_resolver
        self.recording_upload_timeout_seconds = recording_upload_timeout_seconds
        self._inbound: "asyncio.Queue[Tuple[Envelope, object]]" = asyncio.Queue()
        self._recording_events: "asyncio.Queue[ControlEvent]" = asyncio.Queue()
        self.sent: list[ControlEvent] = []
        self.directives: list[object] = []
        self._seq = 0
        self._session_end_reason: Optional[str] = None
        # Virtual time seeded from the injected clock; it then advances by budget
        # increments per event (no wall-clock sleeping, so tests stay deterministic).
        self._ts_ms = int(self.clock.monotonic() * 1000)

    async def execute_recording(self, directive: RecordingStart) -> list[ControlEvent]:
        """Execute one media-plane recording directive through real HTTP."""
        if directive.container != SIMULATOR_RECORDING_CONTAINER:
            return [
                self._emit(
                    "recording.failed",
                    RecordingFailed(
                        recording_id=directive.recording_id,
                        error_code="unsupported_container",
                        retryable=False,
                    ),
                )
            ]
        events = [
            self._emit(
                "recording.started",
                RecordingStarted(
                    recording_id=directive.recording_id,
                    leg=directive.leg,
                    blob_id=directive.blob_id,
                    consent_ref=directive.consent_ref,
                ),
            )
        ]
        try:
            if self.recording_upload_resolver is None:
                raise KeyError("recording upload resolver is not configured")
            upload_target = self.recording_upload_resolver(directive.upload_url_ref)
        except KeyError:
            events.append(
                self._emit(
                    "recording.failed",
                    RecordingFailed(
                        recording_id=directive.recording_id,
                        error_code="upload_target_missing",
                        retryable=False,
                    ),
                )
            )
            return events

        if not _is_safe_local_upload_url(upload_target.url):
            events.append(
                self._emit(
                    "recording.failed",
                    RecordingFailed(
                        recording_id=directive.recording_id,
                        error_code="upload_target_invalid",
                        retryable=False,
                    ),
                )
            )
            return events

        wav_bytes = _deterministic_wav(directive.leg)
        upload_log_filter = _SignedUploadLogFilter()
        transport_loggers = (logging.getLogger("httpx"), logging.getLogger("httpcore"))
        for logger in transport_loggers:
            logger.addFilter(upload_log_filter)
        try:
            async with httpx.AsyncClient(
                timeout=self.recording_upload_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.put(
                    upload_target.url,
                    content=wav_bytes,
                    headers=upload_target.headers,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            events.append(
                self._emit(
                    "recording.failed",
                    RecordingFailed(
                        recording_id=directive.recording_id,
                        error_code="upload_failed",
                        retryable=status_code >= 500 or status_code in {408, 429},
                    ),
                )
            )
            return events
        except httpx.HTTPError:
            events.append(
                self._emit(
                    "recording.failed",
                    RecordingFailed(
                        recording_id=directive.recording_id,
                        error_code="upload_failed",
                        retryable=True,
                    ),
                )
            )
            return events
        finally:
            for logger in transport_loggers:
                logger.removeFilter(upload_log_filter)

        events.append(
            self._emit(
                "recording.uploaded",
                RecordingUploaded(
                    recording_id=directive.recording_id,
                    leg=directive.leg,
                    blob_id=directive.blob_id,
                    upload_url_ref=directive.upload_url_ref,
                    duration_ms=SIMULATOR_RECORDING_DURATION_MS,
                    byte_count=len(wav_bytes),
                    sha256=hashlib.sha256(wav_bytes).hexdigest(),
                    container=directive.container,
                    consent_ref=directive.consent_ref,
                ),
            )
        )
        return events

    # -- downstream (session -> gateway) ------------------------------------

    async def send(self, envelope: Envelope, payload: object) -> None:
        self.sent.append(ControlEvent(envelope, payload))
        self.directives.append(payload)
        if isinstance(payload, RecordingStart):
            for event in await self.execute_recording(payload):
                await self._recording_events.put(event)
            return
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
            SessionStarted(
                transport="sim",
                caller="+10000000000",
                codecs=["pcmu"],
                features=["recording"],
            ),
        )
        await asyncio.sleep(0)
        while not self._recording_events.empty():
            yield await self._recording_events.get()
        for digit in self.dtmf_steps:
            yield self._emit("dtmf", Dtmf(digit=digit))
            async for recording_event in self._drain_recording_events():
                yield recording_event
        for result in self.amd_steps:
            yield self._emit("amd.result", result)
            async for recording_event in self._drain_recording_events():
                yield recording_event

        caller_turns = [t for t in self.scenario.turns if t.speaker == "caller"]
        for index, turn in enumerate(caller_turns):
            turn_id = "turn_%d" % index
            activity = self.speaker_activity[index] if self.speaker_activity else None
            async for event in self._caller_turn(
                turn_id,
                turn.text,
                talk_ms=activity.caller_talk_ms if activity is not None else None,
            ):
                yield event
                async for recording_event in self._drain_recording_events():
                    yield recording_event
            if index in self.vad_interrupt_turns:
                # Caller starts talking again while the agent is still THINKING:
                # no response is spoken this turn (the session cancels it on VAD).
                self._ts_ms += self.budgets.gateway_pacing_ms
                yield self._emit(
                    "vad.speech_start",
                    VadSpeechStart(at_ms=self._ts_ms),
                    turn_id=turn_id,
                )
                async for recording_event in self._drain_recording_events():
                    yield recording_event
                # Drain the cancelled turn's directives up to its stream-end
                # barrier so nothing leaks into the next turn (card 64). The
                # session guarantees the barrier even for a cancelled turn.
                await self._collect_turn_directives(turn_id)
                if self._session_end_reason is not None:
                    yield self._emit(
                        "session.ended",
                        SessionEnded(reason=self._session_end_reason),
                    )
                    return
                continue
            async for event in self._agent_response(
                turn_id,
                interrupt=index in self.barge_in_turns,
                talk_ms=activity.agent_talk_ms if activity is not None else None,
            ):
                yield event
                async for recording_event in self._drain_recording_events():
                    yield recording_event
            if self._session_end_reason is not None:
                yield self._emit(
                    "session.ended",
                    SessionEnded(reason=self._session_end_reason),
                )
                return

        async for recording_event in self._drain_recording_events():
            yield recording_event
        yield self._emit("session.ended", SessionEnded(reason="scenario_complete"))

    async def _drain_recording_events(self) -> AsyncIterator[ControlEvent]:
        while not self._recording_events.empty():
            yield await self._recording_events.get()

    async def _caller_turn(
        self, turn_id: str, text: str, *, talk_ms: Optional[int]
    ) -> AsyncIterator[ControlEvent]:
        words = text.split()
        speech_started_ms = self._ts_ms
        yield self._emit(
            "vad.speech_start",
            VadSpeechStart(at_ms=speech_started_ms),
            turn_id=turn_id,
        )
        accumulated = ""
        for position, word in enumerate(words):
            accumulated = (accumulated + " " + word).strip()
            if talk_ms is None:
                self._ts_ms += self.budgets.gateway_pacing_ms
            else:
                self._ts_ms = speech_started_ms + talk_ms * (position + 1) // len(words)
            stability = round((position + 1) / len(words), 6)
            yield self._emit(
                "stt.partial",
                SttPartial(
                    text=accumulated, stability=stability, provider=self.provider
                ),
                turn_id=turn_id,
            )
        if talk_ms is not None:
            self._ts_ms = speech_started_ms + talk_ms
        yield self._emit(
            "vad.speech_end",
            VadSpeechEnd(
                at_ms=self._ts_ms,
                speech_ms=max(0, self._ts_ms - speech_started_ms),
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
            elif isinstance(directive, SessionEnd):
                self._session_end_reason = directive.reason
                return utterances
            # Other directives, including tts.cancel, do not end collection.

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
        self, turn_id: str, *, interrupt: bool, talk_ms: Optional[int]
    ) -> AsyncIterator[ControlEvent]:
        # Collect every clause of the turn (the session's stream-end barrier
        # bounds the wait, so this resolves without deadlock).
        utterances = await self._collect_turn_directives(turn_id)
        if not utterances:
            if interrupt:
                self._ts_ms += self.budgets.gateway_pacing_ms
                yield self._emit(
                    "barge_in",
                    BargeIn(at_ms=self._ts_ms, during="thinking"),
                    turn_id=turn_id,
                )
            return

        # The simulator models a streamed response as one continuous playback.
        yield self._playback(turn_id, utterances[0].utterance_id, "started", 0)
        playback_started_ms = self._ts_ms

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

        if talk_ms is not None:
            event_count = len(utterances) + 1
            for position, utterance in enumerate(utterances, start=1):
                self._ts_ms = playback_started_ms + talk_ms * position // event_count
                yield self._emit(
                    "tts.playback",
                    TtsPlayback(
                        utterance_id=utterance.utterance_id,
                        state="mark",
                        mark_chars=len(utterance.text),
                    ),
                    turn_id=turn_id,
                )
            last = utterances[-1]
            self._ts_ms = playback_started_ms + talk_ms
            yield self._emit(
                "tts.playback",
                TtsPlayback(
                    utterance_id=last.utterance_id,
                    state="finished",
                    mark_chars=len(last.text),
                ),
                turn_id=turn_id,
            )
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


def _deterministic_wav(leg: str) -> bytes:
    amplitude = SIMULATOR_RECORDING_AMPLITUDES[leg]
    frames = b"".join(
        struct.pack("<h", amplitude if index % 2 == 0 else -amplitude)
        for index in range(SIMULATOR_RECORDING_FRAMES)
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SIMULATOR_RECORDING_SAMPLE_RATE_HZ)
        wav.writeframes(frames)
    return output.getvalue()


def _is_safe_local_upload_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False
