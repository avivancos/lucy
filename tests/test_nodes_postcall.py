import pytest

from lucy.clock import ManualClock
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.mcp import McpClient
from lucy.nodes.postcall import (
    CrmSyncConfig,
    CrmSyncNode,
    DispositionConfig,
    DispositionNode,
    EvalHookConfig,
    EvalHookNode,
    PostCallOnlyError,
    SummaryConfig,
    SummaryNode,
)
from lucy.providers import default_model_registry
from lucy.runtime import TurnContext
from lucy.specs import FunnelStage
from lucy.state import ConversationState, TranscriptLine
from lucy.testing import LocalMcpCommandTransport
from lucy.tools import McpToolExecutor, ToolDef, ToolProfile


def _summary_node(clock: ManualClock) -> SummaryNode:
    return SummaryNode(
        LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=["Caller booked Tuesday."],
                    usage=UsageReport(3, 3),
                )
            ],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        "openai",
        "gpt-5-mini",
        SummaryConfig(),
    )


async def test_summary_node_writes_summary_after_session_end():
    clock = ManualClock()
    node = _summary_node(clock)
    state = ConversationState(
        transcript=[TranscriptLine(speaker="caller", text="Tuesday works")]
    )

    with pytest.raises(PostCallOnlyError):
        await node(state, TurnContext(payload={}, clock=clock))

    update = await node(
        state,
        TurnContext(payload={"session_ended": True}, clock=clock),
    )

    assert update["agent_state"]["summary"] == "Caller booked Tuesday."


async def test_crm_sync_pushes_crm_ready_payload_via_mcp_audited():
    clock = ManualClock()
    transport = LocalMcpCommandTransport()
    tool = ToolDef(
        server="crm",
        name="sync",
        description="Sync CRM",
        json_schema={},
        profile=ToolProfile(expected_latency_ms=10, deadline_ms=100),
    )
    client = McpClient(transport, allowed_tools=[tool.key])
    node = CrmSyncNode(
        tool,
        McpToolExecutor(client, clock),
        CrmSyncConfig(lead_id_slot="lead_id"),
    )
    state = ConversationState(
        slots={"lead_id": "lead-1"},
        funnel_stage=FunnelStage.BOOKED,
        agent_state={
            "sentiment": {
                "label": "positive",
                "confidence": 0.9,
                "model": "local/test",
            }
        },
    )

    update = await node(
        state,
        TurnContext(
            payload={"session_ended": True},
            session_id="session-1",
            clock=clock,
        ),
    )

    assert update["tool_results"][0]["ok"] is True
    assert transport.commands[0]["arguments"]["funnel_stage"] == "booked"
    assert len(client.audit_log) == 1 and client.audit_log[0].allowed is True


async def test_disposition_maps_funnel_stage_to_configured_code():
    node = DispositionNode(
        DispositionConfig(
            mapping={FunnelStage.BOOKED: "BOOKED_OK"},
            default="UNKNOWN",
        )
    )

    update = await node(
        ConversationState(funnel_stage=FunnelStage.BOOKED),
        TurnContext(payload={"session_ended": True}),
    )

    assert update["agent_state"]["disposition"] == "BOOKED_OK"


async def test_eval_hook_scores_attached_scenario():
    emitted = []
    scenario = SyntheticCallScenario(
        name="booked",
        objective="book",
        turns=[SyntheticTurn(speaker="caller", text="Book")],
        expected_outcome="booked",
    )
    node = EvalHookNode(EvalHookConfig())
    state = ConversationState(
        funnel_stage=FunnelStage.BOOKED,
        agent_state={
            "scenario": scenario.model_dump(mode="json"),
            "rag_grounded": True,
            "policy_adhered": True,
        },
    )

    update = await node(
        state,
        TurnContext(payload={"session_ended": True}, emit=emitted.append),
    )

    assert update["agent_state"]["eval_result"]["passed"] is True
    assert emitted[0].passed is True
