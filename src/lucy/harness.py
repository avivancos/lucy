"""Conversation harness (ADR 0011): run a synthetic scenario end-to-end.

Wires a :class:`LocalGatewaySimulator` to a :class:`VoiceSession` on a shared
clock and returns everything the M0 walking skeleton produces - transcript,
per-turn records, spans, and latency waterfalls - so evals and demos drive a
real call with zero keys and zero wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from lucy.clock import Clock, ManualClock
from lucy.drivers import TurnDriver
from lucy.evals import SyntheticCallScenario
from lucy.metrics import LatencyWaterfall
from lucy.session import Responder, TurnRecord, VoiceSession
from lucy.settings import LatencyBudgets
from lucy.tracing import Span
from lucy.transport.dev_gateway import LocalGatewaySimulator


@dataclass
class HarnessResult:
    transcript: List[Tuple[str, str]]  # ("caller" | "agent", text) in order
    turn_records: List[TurnRecord]
    spans: List[Span]
    waterfalls: List[LatencyWaterfall]


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
    ) -> HarnessResult:
        clock = clock or ManualClock()
        gateway = LocalGatewaySimulator(
            scenario,
            clock,
            session_id=self.session_id,
            barge_in_turns=barge_in_turns,
        )
        session = VoiceSession(
            self.session_id,
            gateway,
            responder,
            driver=driver,
            tracer=tracer,
            clock=clock,
            budgets=budgets,
        )
        records = await session.run()

        transcript: List[Tuple[str, str]] = []
        for record in records:
            transcript.append(("caller", record.user_text))
            transcript.append(("agent", record.assistant_text))

        return HarnessResult(
            transcript=transcript,
            turn_records=records,
            spans=session.spans,
            waterfalls=[record.waterfall for record in records],
        )
