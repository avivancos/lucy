"""Synthetic call evaluation primitives."""

from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel


class SyntheticTurn(BaseModel):
    speaker: str
    text: str


class SyntheticCallScenario(BaseModel):
    name: str
    objective: str
    turns: List[SyntheticTurn]
    expected_outcome: str


class EvalEvidence(BaseModel):
    actual_outcome: str
    rag_grounded: bool
    policy_adhered: bool
    interruption_handled: bool = False
    escalated_to_human: bool = False


class EvalRubricResult(BaseModel):
    scenario_name: str
    expected_outcome: str
    actual_outcome: str
    passed: bool
    score: float
    gates: Dict[str, bool]
    reasons: List[str]


def score_synthetic_call(
    scenario: SyntheticCallScenario,
    evidence: EvalEvidence,
) -> EvalRubricResult:
    gates = {
        "outcome": evidence.actual_outcome == scenario.expected_outcome,
        "rag_grounding": evidence.rag_grounded,
        "policy_adherence": evidence.policy_adhered,
    }

    if scenario.expected_outcome == "escalation":
        gates["escalation_behavior"] = evidence.escalated_to_human
    if scenario.expected_outcome == "interruption":
        gates["interruption_handling"] = evidence.interruption_handled
    if scenario.expected_outcome == "failed_booking":
        gates["failed_booking_behavior"] = (
            evidence.actual_outcome == "failed_booking" and evidence.policy_adhered
        )

    reason_labels = {
        "outcome": "outcome mismatch",
        "rag_grounding": "rag grounding failed",
        "policy_adherence": "policy adherence failed",
        "escalation_behavior": "escalation behavior failed",
        "interruption_handling": "interruption handling failed",
        "failed_booking_behavior": "failed booking behavior failed",
    }
    reasons = [reason_labels[name] for name, passed in gates.items() if not passed]
    passed_gate_count = sum(1 for passed in gates.values() if passed)

    return EvalRubricResult(
        scenario_name=scenario.name,
        expected_outcome=scenario.expected_outcome,
        actual_outcome=evidence.actual_outcome,
        passed=not reasons,
        score=round(passed_gate_count / len(gates), 6),
        gates=gates,
        reasons=reasons,
    )


def booking_happy_path() -> SyntheticCallScenario:
    return SyntheticCallScenario(
        name="booking_happy_path",
        objective="Qualify the lead and book an appointment.",
        turns=[
            SyntheticTurn(speaker="caller", text="I want to book a demo."),
            SyntheticTurn(speaker="agent", text="Happy to help. What day works?"),
            SyntheticTurn(speaker="caller", text="Tuesday morning."),
        ],
        expected_outcome="booked",
    )


def default_sales_booking_scenarios() -> List[SyntheticCallScenario]:
    return [
        booking_happy_path(),
        SyntheticCallScenario(
            name="booking_objection",
            objective="Handle a pricing objection without inventing discounts.",
            turns=[
                SyntheticTurn(speaker="caller", text="This sounds expensive."),
                SyntheticTurn(speaker="agent", text="I can explain the options."),
            ],
            expected_outcome="objection",
        ),
        SyntheticCallScenario(
            name="booking_interruption",
            objective="Stop speaking when the caller interrupts.",
            turns=[
                SyntheticTurn(speaker="agent", text="Let me explain how this works."),
                SyntheticTurn(speaker="caller", text="Actually, I just need Tuesday."),
            ],
            expected_outcome="interruption",
        ),
        SyntheticCallScenario(
            name="booking_silence",
            objective="Recover gracefully from caller silence.",
            turns=[
                SyntheticTurn(speaker="caller", text=""),
                SyntheticTurn(speaker="agent", text="Are you still there?"),
            ],
            expected_outcome="silence",
        ),
        SyntheticCallScenario(
            name="booking_escalation",
            objective="Escalate when the caller asks for a human.",
            turns=[
                SyntheticTurn(speaker="caller", text="I want to talk to a person."),
                SyntheticTurn(speaker="agent", text="I will connect you now."),
            ],
            expected_outcome="escalation",
        ),
        SyntheticCallScenario(
            name="booking_failed",
            objective="Report failure when no requested slot is available.",
            turns=[
                SyntheticTurn(speaker="caller", text="I only can do Sunday at 2 AM."),
                SyntheticTurn(speaker="agent", text="That slot is not available."),
            ],
            expected_outcome="failed_booking",
        ),
    ]
