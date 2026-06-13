import pytest
from pydantic import ValidationError

from lucy.specs import (
    AgentSpec,
    CrmSpec,
    EvalSpec,
    FunnelStage,
    LucySpec,
    McpSpec,
    ObservabilitySpec,
    RagSpec,
    VoiceSpec,
)


def test_lucy_spec_serializes():
    spec = LucySpec(
        agent=AgentSpec(
            name="Booking Agent",
            goal="Book qualified sales appointments.",
            prompt="Help the caller book a meeting.",
        ),
        voice=VoiceSpec(
            transport="webrtc",
            stt_provider="deepgram",
            tts_provider="elevenlabs",
        ),
    )

    data = spec.model_dump()

    assert data["agent"]["name"] == "Booking Agent"
    assert data["observability"]["primary_metric"] == "cost_per_minute"
    assert data["rag"]["speculative_prefetch"] is True


def test_agent_spec_rejects_blank_prompt():
    with pytest.raises(ValidationError):
        AgentSpec(name="Agent", goal="Book calls", prompt=" ")


def test_specs_are_orthogonal_and_serializable():
    spec = LucySpec(
        agent=AgentSpec(
            name="Booking Agent",
            goal="Book qualified demos.",
            prompt="Qualify and book.",
            tools=["crm.book_meeting"],
        ),
        voice=VoiceSpec(
            transport="webrtc",
            stt_provider="deepgram",
            tts_provider="elevenlabs",
        ),
        rag=RagSpec(sources=["booking_policy"], max_chunks=3),
        mcp=McpSpec(servers=["crm"], allowed_tools=["crm.book_meeting"]),
        crm=CrmSpec(provider="pili", lead_id_field="lead_id"),
        observability=ObservabilitySpec(trace_sample_rate=0.5),
        evals=EvalSpec(scenarios=["booking_happy_path"]),
    )

    data = spec.model_dump()

    assert data["voice"]["transport"] == "webrtc"
    assert data["rag"]["sources"] == ["booking_policy"]
    assert data["mcp"]["allowed_tools"] == ["crm.book_meeting"]
    assert data["crm"]["funnel_stages"] == list(FunnelStage)
    assert data["observability"]["primary_metric"] == "cost_per_minute"
    assert data["evals"]["scenarios"] == ["booking_happy_path"]


def test_voice_modulation_bounds_are_validated():
    with pytest.raises(ValidationError):
        VoiceSpec(
            transport="webrtc",
            stt_provider="deepgram",
            tts_provider="elevenlabs",
            modulation={"pace": 4.0},
        )
