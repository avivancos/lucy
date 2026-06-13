from lucy.evals import (
    EvalEvidence,
    booking_happy_path,
    default_sales_booking_scenarios,
    score_synthetic_call,
)


def test_booking_happy_path_serializes():
    scenario = booking_happy_path()

    data = scenario.model_dump()

    assert data["name"] == "booking_happy_path"
    assert data["expected_outcome"] == "booked"
    assert data["turns"][0]["speaker"] == "caller"


def test_default_sales_booking_scenarios_cover_core_edges():
    scenarios = default_sales_booking_scenarios()

    outcomes = {scenario.expected_outcome for scenario in scenarios}
    names = {scenario.name for scenario in scenarios}

    assert {
        "booked",
        "objection",
        "interruption",
        "silence",
        "escalation",
        "failed_booking",
    } <= outcomes
    assert len(names) == len(scenarios)


def test_eval_rubric_scores_booking_happy_path_with_typed_gates():
    result = score_synthetic_call(
        booking_happy_path(),
        EvalEvidence(
            actual_outcome="booked",
            rag_grounded=True,
            policy_adhered=True,
        ),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.gates == {
        "outcome": True,
        "rag_grounding": True,
        "policy_adherence": True,
    }
    assert result.reasons == []


def test_eval_rubric_covers_escalation_interruption_and_failed_booking():
    scenarios = {
        scenario.expected_outcome: scenario
        for scenario in default_sales_booking_scenarios()
    }

    escalation = score_synthetic_call(
        scenarios["escalation"],
        EvalEvidence(
            actual_outcome="escalation",
            rag_grounded=True,
            policy_adhered=True,
            escalated_to_human=True,
        ),
    )
    interruption = score_synthetic_call(
        scenarios["interruption"],
        EvalEvidence(
            actual_outcome="interruption",
            rag_grounded=True,
            policy_adhered=True,
            interruption_handled=True,
        ),
    )
    failed_booking = score_synthetic_call(
        scenarios["failed_booking"],
        EvalEvidence(
            actual_outcome="booked",
            rag_grounded=True,
            policy_adhered=False,
        ),
    )

    assert escalation.passed is True
    assert escalation.gates["escalation_behavior"] is True
    assert interruption.passed is True
    assert interruption.gates["interruption_handling"] is True
    assert failed_booking.passed is False
    assert failed_booking.gates["outcome"] is False
    assert "policy adherence failed" in failed_booking.reasons
