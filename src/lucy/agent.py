"""VoiceAgent - the curated SDK facade (card 26).

Build a working voice agent from a :class:`~lucy.specs.LucySpec` in a few lines
with zero API keys: provider strings resolve to the deterministic
``lucy.testing`` simulators, the runtime is wired to a tracer, and a session
exposes a turn-shaped API. A "turn" is one exchange - the caller speaks
(``user_audio``), the agent thinks (the graph), and the agent answers
(``synthesize``) - emitted as a single ``turn`` telemetry event with the full
latency waterfall and any provider timeouts from either half.

Real provider plugins resolve through the same string seam in card 28.
"""

from __future__ import annotations

import hashlib
import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple, cast

from lucy.metrics import LatencyWaterfall
from lucy.observe import Tracer, configure
from lucy.runtime import GraphContext, GraphExecutor, GraphNode
from lucy.specs import LucySpec
from lucy.voice import (
    AudioChunk,
    ProviderTimeoutEvent,
    SttProvider,
    TranscriptEvent,
    TtsProvider,
    VoiceEvent,
)

# Bare provider names resolvable today; real providers arrive as plugins (card 28).
KNOWN_PROVIDERS: Tuple[str, ...] = ("local",)

# Graph node whose result is the agent's spoken response, and whose latency is
# the LLM slice of the waterfall.
RESPONSE_NODE = "llm"
DEFAULT_STT_DEADLINE_MS = 500
DEFAULT_TTS_DEADLINE_MS = 500


def _unknown_provider_message(kind: str, name: str) -> str:
    return (
        "unknown %s provider %r; known providers: %s "
        "(real provider plugins arrive with card 28)"
        % (kind, name, ", ".join(KNOWN_PROVIDERS))
    )


def _resolve_stt(name: str) -> SttProvider:
    if name == "local":
        from lucy.testing import LocalSttSimulator

        return LocalSttSimulator()
    raise ValueError(_unknown_provider_message("stt", name))


def _resolve_tts(name: str) -> TtsProvider:
    if name == "local":
        from lucy.testing import LocalTtsSimulator

        return LocalTtsSimulator()
    raise ValueError(_unknown_provider_message("tts", name))


def _default_graph(tracer: Tracer) -> GraphExecutor:
    async def respond(context: GraphContext) -> str:
        transcript = context.payload.get("transcript", "")
        if transcript:
            return "You said: %s" % transcript
        return "Hello, how can I help?"

    return GraphExecutor(
        [GraphNode(name=RESPONSE_NODE, handler=respond)], tracer=tracer
    )


@dataclass
class _PendingTurn:
    turn_id: str
    turn_index: int
    stt_ms: float = 0.0
    llm_ms: float = 0.0
    mcp_tools_ms: float = 0.0
    tts_ms: float = 0.0
    timeout_events: List[str] = field(default_factory=list)


def _last_transcript(events: Sequence[VoiceEvent]) -> Optional[str]:
    text: Optional[str] = None
    for event in events:
        if isinstance(event, TranscriptEvent) and event.is_final:
            text = event.text
    return text


def _graph_slices(context: GraphContext) -> Tuple[float, float, Optional[str]]:
    """Return (llm_ms, mcp_tools_ms, response) for a completed graph run."""
    llm_ms = 0.0
    mcp_ms = 0.0
    for event in context.trace:
        if event.node.startswith("tool") or event.node.startswith("mcp"):
            mcp_ms += event.latency_ms
        else:
            llm_ms += event.latency_ms
    response = context.results.get(RESPONSE_NODE)
    return llm_ms, mcp_ms, response


class VoiceAgent:
    """A voice agent assembled from a :class:`LucySpec`.

    Pass ``tracer`` to capture telemetry (e.g. in tests); otherwise one is built
    from ``spec.observability``. Pass ``graph`` to override the default
    single-node responder.
    """

    def __init__(
        self,
        spec: LucySpec,
        *,
        tracer: Optional[Tracer] = None,
        graph: Optional[GraphExecutor] = None,
    ) -> None:
        self.spec = spec
        obs = spec.observability
        self.tracer = tracer or configure(
            sample_rate=obs.trace_sample_rate,
            redact_pii=obs.redact_pii,
            record_audio=obs.record_audio,
        )
        self.stt_provider = _resolve_stt(spec.voice.stt_provider)
        self.tts_provider = _resolve_tts(spec.voice.tts_provider)
        self.stt_deadline_ms = DEFAULT_STT_DEADLINE_MS
        self.tts_deadline_ms = DEFAULT_TTS_DEADLINE_MS
        self.graph = graph if graph is not None else _default_graph(self.tracer)
        self._spec_hash = hashlib.sha256(
            spec.model_dump_json().encode("utf-8")
        ).hexdigest()[:16]

    def start_session(self, session_id: str) -> "AgentSession":
        """Open a session: emits ``session.started`` and returns a handle."""
        self.tracer.session_started(
            session_id=session_id,
            agent_name=self.spec.agent.name,
            spec_hash=self._spec_hash,
            environment="local",
            transport=self.spec.voice.transport,
        )
        return AgentSession(self, session_id)


class AgentSession:
    """A single conversation. Turn = ``user_audio`` (listen + think) then
    ``synthesize`` (speak), emitted as one ``turn`` event on completion."""

    def __init__(self, agent: VoiceAgent, session_id: str) -> None:
        self._agent = agent
        self._tracer = agent.tracer
        self.session_id = session_id
        self.last_response: Optional[str] = None
        self._turn_index = 0
        self._started_ms = self._tracer.now_ms()
        self._pending: Optional[_PendingTurn] = None
        self._closed = False

    def __enter__(self) -> "AgentSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _next_turn_id(self) -> str:
        return "%s-t%d" % (self.session_id, self._turn_index)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("session %r is closed" % self.session_id)

    async def user_audio(self, chunk: AudioChunk) -> List[VoiceEvent]:
        """Transcribe inbound audio and run the agent graph, opening a turn.

        Returns the inbound voice events (transcript, STT timeout); the internal
        per-call latency record is consumed into the unified turn and not
        returned. The spoken response is available as ``last_response``. The
        ``turn`` event is emitted when the matching ``synthesize`` completes it.
        """
        self._ensure_open()
        # A new inbound turn while one is still open means the prior turn never
        # got a synthesize; flush it (TTS-less) so no turn is silently dropped.
        if self._pending is not None:
            self._emit_turn(self._pending)
            self._pending = None

        turn = _PendingTurn(turn_id=self._next_turn_id(), turn_index=self._turn_index)
        started = time.perf_counter()
        try:
            events = cast(
                List[VoiceEvent],
                list(
                    await asyncio.wait_for(
                        self._agent.stt_provider.transcribe([chunk]),
                        timeout=self._agent.stt_deadline_ms / 1000,
                    )
                ),
            )
            turn.stt_ms = (time.perf_counter() - started) * 1000
        except asyncio.TimeoutError:
            turn.stt_ms = (time.perf_counter() - started) * 1000
            turn.timeout_events.append("stt:transcribe")
            events = [
                ProviderTimeoutEvent(
                    session_id=chunk.session_id,
                    provider="stt",
                    stage="transcribe",
                    deadline_ms=self._agent.stt_deadline_ms,
                )
            ]

        transcript = _last_transcript(events)
        if transcript is not None:
            context = await self._agent.graph.run(
                {"transcript": transcript, "session_id": self.session_id},
                session_id=self.session_id,
                turn_id=turn.turn_id,
            )
            turn.llm_ms, turn.mcp_tools_ms, self.last_response = _graph_slices(context)
        else:
            self.last_response = None

        self._pending = turn
        return events

    async def synthesize(self, text: str) -> List[VoiceEvent]:
        """Synthesize the agent's response and close the current turn, emitting
        one ``turn`` event with the full STT+LLM+MCP+TTS waterfall.

        Requires an open turn (a preceding ``user_audio``) so the turn count
        tracks caller turns; a stray ``synthesize`` raises rather than
        fabricating a phantom turn.
        """
        self._ensure_open()
        if self._pending is None:
            raise RuntimeError(
                "no open turn to answer; call user_audio() before synthesize()"
            )
        turn = self._pending
        started = time.perf_counter()
        try:
            events = cast(
                List[VoiceEvent],
                list(
                    await asyncio.wait_for(
                        self._agent.tts_provider.synthesize(self.session_id, text),
                        timeout=self._agent.tts_deadline_ms / 1000,
                    )
                ),
            )
        except asyncio.TimeoutError:
            turn.timeout_events.append("tts:synthesize")
            events = [
                ProviderTimeoutEvent(
                    session_id=self.session_id,
                    provider="tts",
                    stage="synthesize",
                    deadline_ms=self._agent.tts_deadline_ms,
                )
            ]
        turn.tts_ms = (time.perf_counter() - started) * 1000
        self._emit_turn(turn)
        self._pending = None
        return events

    def close(self, reason: str = "completed") -> None:
        """Flush any open turn, emit ``session.ended``, and drain the tracer."""
        if self._closed:
            return
        if self._pending is not None:
            self._emit_turn(self._pending)
            self._pending = None
        duration_ms = max(0, self._tracer.now_ms() - self._started_ms)
        self._tracer.session_ended(
            session_id=self.session_id,
            reason=reason,
            duration_ms=duration_ms,
            billable_audio_minutes=0.0,
        )
        self._tracer.flush()
        self._closed = True

    def _emit_turn(self, turn: _PendingTurn) -> None:
        if self._tracer.enabled:
            self._tracer.turn(
                session_id=self.session_id,
                turn_id=turn.turn_id,
                turn_index=turn.turn_index,
                latency_waterfall=LatencyWaterfall(
                    stt_ms=turn.stt_ms,
                    llm_ms=turn.llm_ms,
                    mcp_tools_ms=turn.mcp_tools_ms,
                    tts_ms=turn.tts_ms,
                ),
                interrupted=False,
                timeout_events=turn.timeout_events,
            )
        self._turn_index += 1
