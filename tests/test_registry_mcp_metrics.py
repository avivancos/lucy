import asyncio
import json

import pytest

from lucy.mcp import (
    McpClient,
    McpPermissionError,
    McpSchemaError,
    McpTimeoutError,
    McpToolSchema,
)
from lucy.metrics import (
    CostBreakdown,
    CrmMetricEvent,
    FunnelEvent,
    LatencyWaterfall,
    SentimentScore,
)
from lucy.providers import (
    Capability,
    ModelInfo,
    ModelRegistry,
    default_model_registry,
    revalidate_model_registry,
    registry_revalidation_due,
)
from lucy.specs import FunnelStage, SentimentLabel


def test_model_registry_filters_by_capability():
    registry = default_model_registry()

    stt_models = registry.by_capability(Capability.STT)
    tts_models = registry.by_capability(Capability.TTS)

    assert registry.get("deepgram", "flux") is not None
    assert any(model.provider == "openai" for model in stt_models)
    assert any(model.provider == "elevenlabs" for model in tts_models)


def test_model_registry_has_required_seed_providers_and_version():
    registry = default_model_registry()

    providers = {model.provider for model in registry.models}

    assert registry.version == "2026-06-06"
    for provider in [
        "openai",
        "deepgram",
        "assemblyai",
        "elevenlabs",
        "cartesia",
        "google",
        "anthropic",
        "mistral",
        "groq",
    ]:
        assert provider in providers


def test_model_registry_marks_low_latency_voice_stack():
    registry = default_model_registry()

    low_latency_models = {
        (model.provider, model.model) for model in registry.models if model.low_latency
    }

    assert ("deepgram", "flux") in low_latency_models
    assert ("openai", "gpt-realtime") in low_latency_models
    assert ("elevenlabs", "flash-v2.5") in low_latency_models
    assert ("cartesia", "sonic") in low_latency_models


def test_model_registry_revalidation_reports_catalog_changes_without_mutation():
    checked_in = ModelRegistry(
        version="2026-06-01",
        models=[
            ModelInfo(
                provider="voiceco",
                model="old-stt",
                capabilities=[Capability.STT],
                recommended_for=["streaming"],
            ),
            ModelInfo(
                provider="voiceco",
                model="stable-llm",
                capabilities=[Capability.LLM],
                recommended_for=["reasoning"],
            ),
            ModelInfo(
                provider="voiceco",
                model="talker",
                capabilities=[Capability.TTS],
                recommended_for=["speech"],
            ),
        ],
    )
    observed = ModelRegistry(
        version="fixture-2026-06-20",
        models=[
            ModelInfo(
                provider="voiceco",
                model="renamed-stt",
                capabilities=[Capability.STT],
                recommended_for=["streaming"],
            ),
            ModelInfo(
                provider="voiceco",
                model="stable-llm",
                capabilities=[Capability.LLM],
                recommended_for=["reasoning"],
            ),
            ModelInfo(
                provider="voiceco",
                model="talker",
                capabilities=[Capability.TTS, Capability.REALTIME],
                recommended_for=["speech"],
            ),
            ModelInfo(
                provider="voiceco",
                model="new-tts",
                capabilities=[Capability.TTS],
                recommended_for=["fast speech"],
            ),
        ],
    )

    report = revalidate_model_registry(
        checked_in,
        observed,
        rename_hints={("voiceco", "old-stt"): ("voiceco", "renamed-stt")},
    )

    assert report.checked_in_version == "2026-06-01"
    assert report.observed_version == "fixture-2026-06-20"
    assert report.added == ["voiceco/new-tts"]
    assert report.removed == []
    assert report.renamed == ["voiceco/old-stt -> voiceco/renamed-stt"]
    assert report.capability_changed == ["voiceco/talker: tts -> realtime,tts"]
    assert checked_in.get("voiceco", "old-stt") is not None
    assert checked_in.get("voiceco", "new-tts") is None


def test_model_registry_revalidation_due_uses_registry_version_date():
    registry = ModelRegistry(version="2026-06-01", models=[])

    assert (
        registry_revalidation_due(
            registry,
            as_of="2026-07-10",
            max_age_days=30,
        )
        is True
    )
    assert (
        registry_revalidation_due(
            registry,
            as_of="2026-06-15",
            max_age_days=30,
        )
        is False
    )


def test_cost_per_minute_uses_all_components():
    cost = CostBreakdown(
        stt_cost=1,
        llm_cost=2,
        tts_cost=3,
        telephony_cost=4,
        rag_cost=5,
        mcp_tool_cost=6,
        infra_cost=7,
        billable_audio_minutes=4,
    )

    assert cost.total_cost == 28
    assert cost.cost_per_minute == 7


def test_latency_waterfall_total():
    waterfall = LatencyWaterfall(stt_ms=1, rag_ms=2, llm_ms=3, tts_ms=4)

    assert waterfall.total_ms == 10


def test_sentiment_funnel_and_crm_metric_contracts():
    sentiment = SentimentScore(
        label=SentimentLabel.NEGATIVE,
        confidence=0.77,
        model="registry:sentiment-default",
    )
    funnel = FunnelEvent(
        session_id="sess_demo",
        stage=FunnelStage.ESCALATION,
        confidence=0.88,
        crm_payload={"lead_id": "lead_demo", "reason": "negative sentiment"},
    )
    metric = CrmMetricEvent(
        session_id="sess_demo",
        lead_id="lead_demo",
        sentiment=sentiment,
        funnel=funnel,
        emitted_at_ms=1200,
    )

    assert metric.sentiment.label == SentimentLabel.NEGATIVE
    assert metric.funnel.stage == FunnelStage.ESCALATION
    assert metric.crm_ready_payload == {
        "session_id": "sess_demo",
        "lead_id": "lead_demo",
        "sentiment": "negative",
        "funnel_stage": "escalation",
        "confidence": "0.88",
    }


def test_booking_funnel_event_is_crm_ready():
    funnel = FunnelEvent(
        session_id="sess_demo",
        stage=FunnelStage.BOOKED,
        confidence=0.91,
        crm_payload={"lead_id": "lead_demo", "booking_id": "booking_demo"},
    )

    assert funnel.stage == FunnelStage.BOOKED
    assert funnel.crm_payload["booking_id"] == "booking_demo"


class LocalMcpTransport:
    async def call_tool(self, server, tool, arguments):
        return {"server": server, "tool": tool, "arguments": arguments}


def test_mcp_client_allows_and_audits_tool_call():
    client = McpClient(LocalMcpTransport(), allowed_tools=["crm.book_meeting"])

    result = asyncio.run(
        client.call_tool("crm", "book_meeting", {"lead_id": "lead_demo"})
    )

    assert result["tool"] == "book_meeting"
    assert client.audit_log[0].allowed is True


def test_mcp_client_denies_unknown_tool():
    client = McpClient(LocalMcpTransport(), allowed_tools=["crm.book_meeting"])

    with pytest.raises(McpPermissionError):
        asyncio.run(client.call_tool("crm", "delete_everything", {}))

    assert client.audit_log[0].allowed is False


def test_mcp_client_rejects_malformed_payload_with_typed_error():
    client = McpClient(
        LocalMcpTransport(),
        allowed_tools=["crm.upsert_lead"],
        tool_schemas={
            "crm.upsert_lead": McpToolSchema(
                required_fields={"lead_id": str, "session_id": str}
            )
        },
    )

    with pytest.raises(McpSchemaError, match="missing required argument: session_id"):
        asyncio.run(client.call_tool("crm", "upsert_lead", {"lead_id": "lead_demo"}))

    assert client.audit_log[0].allowed is True
    assert client.audit_log[0].error == "missing required argument: session_id"


class SlowMcpTransport:
    async def call_tool(self, server, tool, arguments):
        await asyncio.sleep(0.05)
        return {"server": server, "tool": tool, "arguments": arguments}


def test_mcp_client_respects_explicit_deadline():
    client = McpClient(SlowMcpTransport(), allowed_tools=["crm.upsert_lead"])

    with pytest.raises(McpTimeoutError, match="deadline exceeded"):
        asyncio.run(
            client.call_tool(
                "crm",
                "upsert_lead",
                {"lead_id": "lead_demo"},
                timeout_ms=1,
            )
        )

    assert client.audit_log[0].allowed is True
    assert client.audit_log[0].error == "deadline exceeded"


def test_mcp_client_writes_allowed_and_denied_replay_file(tmp_path):
    client = McpClient(LocalMcpTransport(), allowed_tools=["crm.upsert_lead"])

    asyncio.run(client.call_tool("crm", "upsert_lead", {"lead_id": "lead_demo"}))
    with pytest.raises(McpPermissionError):
        asyncio.run(client.call_tool("crm", "delete_lead", {"lead_id": "lead_demo"}))

    replay_path = tmp_path / "mcp-replay.json"
    client.write_replay_file(replay_path)

    replay = json.loads(replay_path.read_text())

    assert [event["allowed"] for event in replay["events"]] == [True, False]
    assert replay["events"][0]["result"]["tool"] == "upsert_lead"
    assert replay["events"][1]["error"] == "tool not allowed"
