import pytest
from pydantic import ValidationError

from lucy.specs import (
    AgentSpec,
    AsteriskTransportMode,
    AsteriskTransportSpec,
    CrmSpec,
    EvalSpec,
    FunnelStage,
    LucySpec,
    McpSpec,
    ObservabilitySpec,
    RagSpec,
    VoiceSpec,
    resolve_asterisk_transport,
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


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        ("asterisk/audiosocket", AsteriskTransportMode.AUDIO_SOCKET),
        ("asterisk/media_websocket", AsteriskTransportMode.MEDIA_WEBSOCKET),
        ("asterisk/ari_external_media", AsteriskTransportMode.ARI_EXTERNAL_MEDIA),
    ],
)
def test_asterisk_transport_resolves_registered_spec_strings(value, mode):
    assert resolve_asterisk_transport(value) == AsteriskTransportSpec(mode=mode)


def test_asterisk_transport_resolves_typed_spec_without_losing_identity():
    transport = AsteriskTransportSpec(mode=AsteriskTransportMode.MEDIA_WEBSOCKET)

    assert resolve_asterisk_transport(transport) is transport
    assert transport.spec_string == "asterisk/media_websocket"


@pytest.mark.parametrize(
    "value",
    [
        "asterisk",
        "asterisk/unknown",
        "asterisk/audiosocket/extra",
        "asterisk /audiosocket",
        "other/audiosocket",
    ],
)
def test_asterisk_transport_rejects_unregistered_or_malformed_specs(value):
    with pytest.raises(ValueError, match="Asterisk transport spec"):
        resolve_asterisk_transport(value)


def test_voice_spec_preserves_frozen_string_transport_abi():
    voice = VoiceSpec(
        transport="asterisk/audiosocket",
        stt_provider="deepgram",
        tts_provider="elevenlabs",
    )

    assert voice.transport == "asterisk/audiosocket"
    assert voice.model_dump(mode="json")["transport"] == "asterisk/audiosocket"


def test_voice_spec_resolves_asterisk_transport_string_through_registry():
    voice = VoiceSpec(
        transport="asterisk/ari_external_media",
        stt_provider="deepgram",
        tts_provider="elevenlabs",
    )

    assert voice.transport == "asterisk/ari_external_media"


def test_voice_spec_rejects_unknown_asterisk_transport_string():
    with pytest.raises(ValidationError, match="Asterisk transport spec"):
        VoiceSpec(
            transport="asterisk/unknown",
            stt_provider="deepgram",
            tts_provider="elevenlabs",
        )
