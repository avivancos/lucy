"""Conversation harness (ADR 0011): run a synthetic scenario end-to-end.

Wires a :class:`LocalGatewaySimulator` to a :class:`VoiceSession` on a shared
clock and returns everything the M0 walking skeleton produces - transcript,
per-turn records, spans, and latency waterfalls - so evals and demos drive a
real call with zero keys and zero wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, List, Mapping, Optional, Sequence, Tuple

from lucy.clock import Clock, ManualClock
from lucy.drivers import TurnDriver
from lucy.evals import EvalEvidence, SyntheticCallScenario
from lucy.metrics import CostComponent, LatencyWaterfall
from lucy.pricing import PriceBook, TelephonyDirection
from lucy.providers import ModelRegistry, ProviderIdentity
from lucy.rag import SpeculativeRagNode
from lucy.session import Responder, TurnRecord, VoiceSession
from lucy.settings import LatencyBudgets, SpeculationSettings
from lucy.state import Checkpoint, ConversationState
from lucy.tracing import Span
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import ControlEvent, Envelope, SttFinal, TtsCancel, TtsSpeak


@dataclass
class HarnessResult:
    transcript: List[Tuple[str, str]]  # ("caller" | "agent", text) in order
    turn_records: List[TurnRecord]
    spans: List[Span]
    waterfalls: List[LatencyWaterfall]
    directives: List[ControlEvent]
    events: List[ControlEvent] = field(default_factory=list)
    checkpoints: List[Checkpoint] = field(default_factory=list)


@dataclass(frozen=True)
class ReplayResult:
    final_state: ConversationState
    matches_final_checkpoint: bool
    diverged_at_checkpoint_id: Optional[str]


class _RecordingTransport:
    def __init__(self, gateway: LocalGatewaySimulator) -> None:
        self.gateway = gateway
        self.events_seen: List[ControlEvent] = []

    @property
    def sent(self) -> List[ControlEvent]:
        return self.gateway.sent

    async def events(self) -> AsyncIterator[ControlEvent]:
        async for event in self.gateway.events():
            self.events_seen.append(event)
            yield event

    async def send(self, envelope: Envelope, payload: object) -> None:
        await self.gateway.send(envelope, payload)


class ConversationHarness:
    def __init__(self, session_id: str = "harness_session") -> None:
        self.session_id = session_id

    async def run(
        self,
        scenario: SyntheticCallScenario,
        responder: Optional[Responder] = None,
        *,
        driver: Optional[TurnDriver] = None,
        clock: Optional[Clock] = None,
        tracer=None,
        budgets: Optional[LatencyBudgets] = None,
        barge_in_turns: Iterable[int] = (),
        vad_interrupt_turns: Iterable[int] = (),
        pricebook: Optional[PriceBook] = None,
        telephony_direction: TelephonyDirection = TelephonyDirection.INBOUND,
        rag: Optional[SpeculativeRagNode] = None,
        speculation: Optional[SpeculationSettings] = None,
        provider_attribution: Optional[Mapping[CostComponent, ProviderIdentity]] = None,
        provider_registry: Optional[ModelRegistry] = None,
    ) -> HarnessResult:
        clock = clock or ManualClock()
        if not tuple(barge_in_turns) and scenario.expected_outcome == "interruption":
            barge_in_turns = (0,)
        gateway = LocalGatewaySimulator(
            scenario,
            clock,
            session_id=self.session_id,
            barge_in_turns=barge_in_turns,
            vad_interrupt_turns=vad_interrupt_turns,
        )
        transport = _RecordingTransport(gateway)
        session = VoiceSession(
            self.session_id,
            transport,
            responder,
            driver=driver,
            tracer=tracer,
            clock=clock,
            budgets=budgets,
            pricebook=pricebook,
            telephony_direction=telephony_direction,
            rag=rag,
            speculation=speculation,
            provider_attribution=provider_attribution,
            provider_registry=provider_registry,
        )
        records = await session.run()
        checkpoints: List[Checkpoint] = []
        checkpointer = getattr(driver, "checkpointer", None) if driver else None
        if checkpointer is not None:
            checkpoint_thread = getattr(driver, "thread_id", self.session_id)
            checkpoints = await checkpointer.history(checkpoint_thread)

        transcript: List[Tuple[str, str]] = []
        for record in records:
            transcript.append(("caller", record.user_text))
            transcript.append(("agent", record.assistant_text))

        return HarnessResult(
            transcript=transcript,
            turn_records=records,
            spans=session.spans,
            waterfalls=[record.waterfall for record in records],
            directives=list(gateway.sent),
            events=list(transport.events_seen),
            checkpoints=checkpoints,
        )

    def replay(
        self,
        checkpoints: Sequence[Checkpoint],
        recorded_events: Sequence[ControlEvent],
    ) -> ReplayResult:
        from lucy.graph import GraphValidationError

        if not checkpoints:
            return ReplayResult(
                final_state=ConversationState(),
                matches_final_checkpoint=True,
                diverged_at_checkpoint_id=None,
            )

        thread_id = checkpoints[0].thread_id
        last_superstep_by_turn: dict[str, int] = {}
        closed_turns: set[str] = set()
        for checkpoint in checkpoints:
            if checkpoint.thread_id != thread_id:
                raise GraphValidationError("replay checkpoints span threads")
            previous = last_superstep_by_turn.get(checkpoint.turn_id)
            if previous is not None and checkpoint.superstep < previous:
                raise GraphValidationError("checkpoint supersteps are not monotonic")
            if checkpoint.turn_id in closed_turns:
                raise GraphValidationError("checkpoint turn order is not monotonic")
            last_superstep_by_turn[checkpoint.turn_id] = checkpoint.superstep
            if checkpoint.kind == "turn_final":
                closed_turns.add(checkpoint.turn_id)

        recorded_callers = [
            event.payload.text
            for event in recorded_events
            if isinstance(event.payload, SttFinal)
        ]
        final_state = ConversationState()
        for checkpoint in checkpoints:
            if checkpoint.kind != "turn_final":
                continue
            final_state = checkpoint.state
            caller_lines = [
                line.text for line in final_state.transcript if line.speaker == "caller"
            ]
            if caller_lines != recorded_callers[: len(caller_lines)]:
                return ReplayResult(
                    final_state=final_state,
                    matches_final_checkpoint=False,
                    diverged_at_checkpoint_id=checkpoint.checkpoint_id,
                )

        last_checkpoint = checkpoints[-1]
        matches = final_state == last_checkpoint.state
        diverged_at = None if matches else last_checkpoint.checkpoint_id
        return ReplayResult(
            final_state=final_state,
            matches_final_checkpoint=matches,
            diverged_at_checkpoint_id=diverged_at,
        )


def evidence_from_result(
    scenario: SyntheticCallScenario,
    result: HarnessResult,
) -> EvalEvidence:
    interrupted = [record for record in result.turn_records if record.interrupted]
    actual_outcome = "interruption" if interrupted else "completed"
    has_cancel = any(
        isinstance(event.payload, TtsCancel) for event in result.directives
    )
    handled = False
    if interrupted and has_cancel:
        record = interrupted[0]
        planned = " ".join(
            event.payload.text
            for event in result.directives
            if isinstance(event.payload, TtsSpeak)
            and event.envelope.turn_id == record.turn_id
        )
        handled = (
            bool(planned)
            and planned.startswith(record.assistant_text)
            and len(record.assistant_text) < len(planned)
        )

    return EvalEvidence(
        actual_outcome=actual_outcome,
        # M3 exercises interruption mechanics only; card 37 adds real evidence
        # for outcome detection, RAG grounding, and policy adherence.
        rag_grounded=True,
        policy_adhered=True,
        interruption_handled=handled,
        escalated_to_human=False,
    )
