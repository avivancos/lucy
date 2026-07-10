from lucy.clock import ManualClock
from lucy.drivers import GraphTurnDriver
from lucy.evals import (
    EvalEvidence,
    default_sales_booking_scenarios,
    score_synthetic_call,
)
from lucy.harness import ConversationHarness, evidence_from_result
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.nodes import (
    DisclosureConfig,
    DispositionConfig,
    DtmfMenuConfig,
    EndCallConfig,
    EndReason,
    SlotSpec,
    TransferConfig,
)
from lucy.prebuilt import (
    BookingAgentConfig,
    LeadQualifierConfig,
    ReceptionistConfig,
    SurveyAgentConfig,
    SurveyQuestion,
    booking_agent,
    lead_qualifier,
    receptionist,
    survey_agent,
)
from lucy.providers import default_model_registry
from lucy.rag import InMemoryRagIndex, RagChunk, SpeculativeRagNode
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets
from lucy.specs import FunnelStage
from lucy.state import ConversationState, TranscriptLine
from lucy.transport.schema import SessionEnd, Transfer, TtsSpeak


PROVIDER = "openai"
MODEL = "gpt-5-mini"


def _llm(clock: ManualClock, *responses: str) -> LocalLlmSimulator:
    return LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=[response], usage=UsageReport(2, 2))
            for response in responses
        ],
        clock,
        token_interval_ms=0,
    )


async def test_booking_agent_passes_all_six_golden_scenarios():
    stage_by_outcome = {
        "booked": FunnelStage.BOOKED,
        "objection": FunnelStage.OBJECTION,
        "interruption": FunnelStage.INTERESTED,
        "silence": FunnelStage.INTERESTED,
        "escalation": FunnelStage.ESCALATION,
        "failed_booking": FunnelStage.FAILED_BOOKING,
    }
    outcomes = {
        scenario.expected_outcome: scenario.expected_outcome
        for scenario in default_sales_booking_scenarios()
    }

    for scenario in default_sales_booking_scenarios():
        clock = ManualClock()
        outcome = scenario.expected_outcome
        stage = stage_by_outcome[outcome]
        responses = []
        caller_turns = sum(turn.speaker == "caller" for turn in scenario.turns)
        for _ in range(caller_turns):
            responses.extend(
                [
                    '{"intent":"%s","confidence":0.99}' % outcome,
                    "I handled %s." % outcome,
                    '{"stage":"%s","confidence":0.99}' % stage.value,
                ]
            )
        llm = _llm(clock, *responses)
        graph = booking_agent(
            llm=llm,
            registry=default_model_registry(),
            provider=PROVIDER,
            model=MODEL,
            rag=SpeculativeRagNode(
                InMemoryRagIndex(
                    [
                        RagChunk(
                            id="policy",
                            source="booking",
                            text=(
                                "I want book demo this sounds expensive actually "
                                "need Tuesday are you still there talk person only "
                                "can do Sunday"
                            ),
                        )
                    ]
                )
            ),
            clock=clock,
            budgets=LatencyBudgets(),
            config=BookingAgentConfig(
                disclosure=DisclosureConfig(template="This call uses AI."),
                slots=[
                    SlotSpec(name="day", pattern=r"(?i)(Tuesday|Sunday)"),
                    SlotSpec(name="time", pattern=r"(?i)(morning|2 AM)"),
                ],
                intents={key: key for key in outcomes},
                outcome_by_intent=outcomes,
                escalation_transfer=TransferConfig(target="human-queue", mode="blind"),
            ),
        )
        driver = GraphTurnDriver(graph, session_id="booking", clock=clock)

        harness_result = await ConversationHarness("booking").run(
            scenario,
            driver=driver,
            clock=clock,
        )
        state = driver.state
        call_evidence = evidence_from_result(scenario, harness_result)
        evidence = EvalEvidence(
            actual_outcome=state.agent_state["actual_outcome"],
            rag_grounded=state.agent_state["rag_grounded"],
            policy_adhered=state.agent_state["policy_adhered"],
            interruption_handled=call_evidence.interruption_handled,
            escalated_to_human=state.agent_state.get("transfer_target")
            == "human-queue",
        )

        assert score_synthetic_call(scenario, evidence).passed is True


async def test_lead_qualifier_fills_slots_and_ends_with_disposition():
    clock = ManualClock()
    emitted = []
    graph = lead_qualifier(
        llm=_llm(
            clock,
            '{"label":"positive","confidence":0.9}',
            '{"stage":"qualified","confidence":0.95}',
        ),
        registry=default_model_registry(),
        provider=PROVIDER,
        model=MODEL,
        config=LeadQualifierConfig(
            slots=[SlotSpec(name="company", pattern=r"company is ([A-Za-z]+)")],
            disposition_by_stage={FunnelStage.QUALIFIED: "SALES_READY"},
            default_disposition="UNQUALIFIED",
            end_call=EndCallConfig(reason=EndReason.COMPLETED),
        ),
    )

    result = await graph.invoke_turn(
        ConversationState(),
        TurnContext(
            payload={"user_text": "My company is Acme"},
            session_id="lead",
            emit=emitted.append,
            clock=clock,
        ),
    )

    assert result.slots["company"] == "Acme"
    assert result.agent_state["disposition"] == "SALES_READY"
    assert result.funnel_stage is FunnelStage.QUALIFIED
    assert emitted[-1] == SessionEnd(reason="completed")


async def test_receptionist_routes_intent_to_transfer_directive():
    emitted = []
    graph = receptionist(
        llm=_llm(ManualClock(), '{"intent":"sales","confidence":0.95}'),
        registry=default_model_registry(),
        provider=PROVIDER,
        model=MODEL,
        config=ReceptionistConfig(
            disclosure=DisclosureConfig(template="AI receptionist."),
            intents={"sales": "Sales department"},
            departments={"sales": TransferConfig(target="sales-queue", mode="blind")},
            confidence_floor=0.8,
            dtmf=DtmfMenuConfig(
                prompt_template="Press one for sales.",
                options={"1": "sales"},
                timeout_ms=100,
            ),
        ),
    )

    await graph.invoke_turn(
        ConversationState(),
        TurnContext(payload={"user_text": "Sales please"}, emit=emitted.append),
    )

    assert Transfer(target="sales-queue") in emitted


async def test_receptionist_falls_back_to_dtmf_menu_on_low_confidence():
    emitted = []
    graph = receptionist(
        llm=_llm(ManualClock(), '{"intent":"sales","confidence":0.2}'),
        registry=default_model_registry(),
        provider=PROVIDER,
        model=MODEL,
        config=ReceptionistConfig(
            disclosure=DisclosureConfig(template="AI receptionist."),
            intents={"sales": "Sales department"},
            departments={"sales": TransferConfig(target="sales-queue", mode="blind")},
            confidence_floor=0.8,
            dtmf=DtmfMenuConfig(
                prompt_template="Press one for sales.",
                options={"1": "sales"},
                timeout_ms=100,
            ),
        ),
    )

    result = await graph.invoke_turn(
        ConversationState(),
        TurnContext(
            payload={"user_text": "Maybe sales", "dtmf_digits": ["1"]},
            emit=emitted.append,
        ),
    )

    assert any(
        isinstance(item, TtsSpeak) and item.text == "Press one for sales."
        for item in emitted
    )
    assert result.agent_state["dtmf_selection"] == "sales"
    assert Transfer(target="sales-queue") in emitted


async def test_survey_agent_collects_answers_and_writes_summary():
    clock = ManualClock()
    emitted = []
    graph = survey_agent(
        llm=_llm(clock, "The caller was satisfied."),
        registry=default_model_registry(),
        provider=PROVIDER,
        model=MODEL,
        config=SurveyAgentConfig(
            questions=[
                SurveyQuestion(
                    slot="rating",
                    prompt="How would you rate us?",
                    pattern=r"(five)",
                ),
                SurveyQuestion(
                    slot="comment",
                    prompt="What should we improve?",
                    pattern=r"(.+)",
                ),
            ],
            completion_template="Thank you.",
            disposition=DispositionConfig(mapping={}, default="SURVEY_COMPLETE"),
        ),
    )

    state = await graph.invoke_turn(
        ConversationState(),
        TurnContext(payload={}, emit=emitted.append, turn_id="one", clock=clock),
    )
    state = await graph.invoke_turn(
        state,
        TurnContext(
            payload={"user_text": "five"},
            emit=emitted.append,
            turn_id="two",
            clock=clock,
        ),
    )
    state = state.merged(
        {
            "transcript": [
                *state.transcript,
                TranscriptLine(speaker="caller", text="Faster support"),
            ]
        }
    )
    state = await graph.invoke_turn(
        state,
        TurnContext(
            payload={"user_text": "Faster support", "session_ended": True},
            emit=emitted.append,
            turn_id="three",
            clock=clock,
        ),
    )

    assert state.slots == {"rating": "five", "comment": "Faster support"}
    assert state.agent_state["summary"] == "The caller was satisfied."
    assert state.agent_state["disposition"] == "SURVEY_COMPLETE"
    assert [item.text for item in emitted if isinstance(item, TtsSpeak)] == [
        "How would you rate us?",
        "What should we improve?",
        "Thank you.",
    ]
