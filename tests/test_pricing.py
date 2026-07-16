import asyncio
import json
import uuid
from types import MappingProxyType

import pytest
from pydantic import ValidationError

import lucy.observe as observe_module
from lucy.clock import ManualClock
from lucy.drivers import (
    CascadedTurnDriver,
    GraphTurnDriver,
    RealtimeSessionConfig,
    RealtimeTurnDriver,
)
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.harness import ConversationHarness
from lucy.graph import AgentGraph, default_agent_graph
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.llm import ToolCallReady
from lucy.limits import (
    MAX_CONTROL_TIMESTAMP_MS,
    MAX_PRICEBOOK_BYTES,
    MAX_PRICE_RATE,
    MAX_USAGE_UNITS,
)
from lucy.mcp import McpClient, McpToolSchema
from lucy.nodes.action import McpToolNode, SayNode
from lucy.observe import Tracer
from lucy.metrics import CostComponent
from lucy.observe.events import CostEvent
from lucy.pricing import (
    PriceBook,
    PriceBookRegistry,
    PricingSettings,
    TelephonyDirection,
    VoiceUsage,
    load_pricebook,
)
from lucy.providers import (
    Capability,
    ModelInfo,
    ModelRegistry,
    ProviderIdentity,
    default_model_registry,
)
from lucy.rag import InMemoryRagIndex, RagChunk, SpeculativeRagNode
from lucy.settings import LatencyBudgets, SpeculationSettings
from lucy.specs import CpaasTransportMode
from lucy.session import VoiceSession
from lucy.testing import (
    InMemoryTraceExporter,
    LocalMcpCommandTransport,
    LocalRealtimeSimulator,
    ScriptedRealtimeTurn,
)
from lucy.tools import McpToolExecutor, ToolDef, ToolProfile
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import (
    ControlEvent,
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    TtsStreamEnd,
    VadSpeechEnd,
    VadSpeechStart,
)

TEST_LLM_PROVIDER = "openai"
TEST_LLM_MODEL = "gpt-5"
TEST_REALTIME_PROVIDER = "openai"
TEST_REALTIME_MODEL = "gpt-realtime"
FIXTURE_STT_PROVIDER = "fixture-stt"
FIXTURE_STT_MODEL = "stt-v9"
FIXTURE_LLM_PROVIDER = "fixture-llm"
FIXTURE_LLM_MODEL = "llm-v9"
FIXTURE_TTS_PROVIDER = "fixture-tts"
FIXTURE_TTS_MODEL = "tts-v9"
FIXTURE_REALTIME_PROVIDER = "fixture-realtime"
FIXTURE_REALTIME_MODEL = "realtime-v9"


def _fixture_registry() -> ModelRegistry:
    return ModelRegistry(
        version="fixture-v1",
        models=[
            ModelInfo(
                provider=FIXTURE_STT_PROVIDER,
                model=FIXTURE_STT_MODEL,
                capabilities=[Capability.STT],
                recommended_for=[],
            ),
            ModelInfo(
                provider=FIXTURE_LLM_PROVIDER,
                model=FIXTURE_LLM_MODEL,
                capabilities=[Capability.LLM],
                recommended_for=[],
            ),
            ModelInfo(
                provider=FIXTURE_TTS_PROVIDER,
                model=FIXTURE_TTS_MODEL,
                capabilities=[Capability.TTS],
                recommended_for=[],
            ),
            ModelInfo(
                provider=FIXTURE_REALTIME_PROVIDER,
                model=FIXTURE_REALTIME_MODEL,
                capabilities=[Capability.REALTIME, Capability.LLM, Capability.TTS],
                recommended_for=[],
            ),
        ],
    )


def _pricebook() -> PriceBook:
    return PriceBook(
        version="voice-2026-07",
        currency="USD",
        llm_prompt_per_1k=0.01,
        llm_cached_prompt_per_1k=0.002,
        llm_completion_per_1k=0.03,
        stt_per_minute=0.006,
        tts_per_1k_characters=0.02,
        telephony_inbound_per_minute=0.015,
        telephony_outbound_per_minute=0.025,
        rag_per_request=0.001,
        mcp_per_call=0.002,
        infra_per_minute=0.004,
    )


def test_pricebook_loads_from_typed_json_setting(tmp_path, monkeypatch):
    path = tmp_path / "voice-prices.json"
    path.write_text(json.dumps(_pricebook().model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("LUCY_PRICING_PRICEBOOK_PATH", str(path))

    loaded = load_pricebook(PricingSettings())

    assert loaded == _pricebook()


def test_pricebook_reload_observes_file_changes(tmp_path):
    path = tmp_path / "voice-prices.json"
    path.write_text(json.dumps(_pricebook().model_dump(mode="json")), encoding="utf-8")
    settings = PricingSettings(pricebook_path=path)
    assert load_pricebook(settings).version == "voice-2026-07"

    updated = _pricebook().model_copy(update={"version": "voice-2026-08"})
    path.write_text(json.dumps(updated.model_dump(mode="json")), encoding="utf-8")

    assert load_pricebook(settings).version == "voice-2026-08"


def test_missing_pricebook_setting_uses_explicit_unpriced_book():
    loaded = load_pricebook(PricingSettings(pricebook_path=None))

    assert loaded.version == "unpriced"
    assert loaded.calculate(VoiceUsage()).cost.total_cost == 0.0


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        '{"version":"v1","unknown_rate":1}',
        '{"version":"","currency":"USD"}',
        '{"version":"v1","currency":"usd"}',
    ],
)
def test_pricebook_file_rejects_malformed_or_unknown_data(tmp_path, contents):
    path = tmp_path / "invalid-pricebook.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValidationError):
        load_pricebook(PricingSettings(pricebook_path=path))


def test_pricebook_file_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_pricebook(PricingSettings(pricebook_path=tmp_path / "missing.json"))


def test_pricebook_file_rejects_symlinks_and_oversized_payloads(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(json.dumps(_pricebook().model_dump(mode="json")))
    link = tmp_path / "linked.json"
    link.symlink_to(target)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * MAX_PRICEBOOK_BYTES + b"}")

    with pytest.raises(OSError):
        load_pricebook(PricingSettings(pricebook_path=link))
    with pytest.raises(ValueError, match="size limit"):
        load_pricebook(PricingSettings(pricebook_path=oversized))


def test_pricebook_rejects_negative_nonfinite_and_ambiguous_tts_prices():
    with pytest.raises(ValidationError):
        PriceBook(version="v1", llm_prompt_per_1k=-0.01)
    with pytest.raises(ValidationError):
        PriceBook(version="v1", stt_per_minute=float("inf"))
    with pytest.raises(ValidationError, match="one TTS billing basis"):
        PriceBook(
            version="v1",
            tts_per_1k_characters=0.01,
            tts_per_second=0.02,
        )
    with pytest.raises(ValidationError, match="cached prompt tokens"):
        VoiceUsage(llm_prompt_tokens=1, llm_cached_prompt_tokens=2)
    with pytest.raises(ValidationError):
        PriceBook(version="v1", llm_prompt_per_1k=MAX_PRICE_RATE + 1)


@pytest.mark.parametrize(
    "usage",
    [
        (True, 0, 0),
        (-1, 0, 0),
        (MAX_USAGE_UNITS + 1, 0, 0),
    ],
)
def test_provider_usage_rejects_ambiguous_or_unbounded_counters(usage):
    with pytest.raises(ValueError, match="bounded nonnegative"):
        UsageReport(*usage)


def test_pricebook_registry_rejects_duplicate_names():
    registry = PriceBookRegistry()
    pricebook = _pricebook()
    registry.register("primary", pricebook)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("primary", _pricebook())

    assert registry.require("primary") is pricebook
    with pytest.raises(KeyError, match="missing"):
        registry.require("missing")


def test_pricebook_calculates_all_components_and_raw_attribution():
    usage = VoiceUsage(
        llm_prompt_tokens=1_000,
        llm_cached_prompt_tokens=500,
        llm_completion_tokens=2_000,
        stt_audio_ms=120_000,
        tts_characters=2_000,
        telephony_minutes=3.0,
        telephony_direction=TelephonyDirection.INBOUND,
        rag_requests=2,
        mcp_tool_calls=3,
        infra_minutes=3.0,
    )

    result = _pricebook().calculate(usage)

    assert result.cost.stt_cost == pytest.approx(0.012)
    assert result.cost.llm_cost == pytest.approx(0.066)
    assert result.cost.tts_cost == pytest.approx(0.04)
    assert result.cost.telephony_cost == pytest.approx(0.045)
    assert result.cost.rag_cost == pytest.approx(0.002)
    assert result.cost.mcp_tool_cost == pytest.approx(0.006)
    assert result.cost.infra_cost == pytest.approx(0.012)
    assert result.attribution == {
        "llm_prompt_tokens": 1000.0,
        "llm_cached_prompt_tokens": 500.0,
        "llm_completion_tokens": 2000.0,
        "stt_audio_minutes": 2.0,
        "tts_characters": 2000.0,
        "tts_audio_seconds": 0.0,
        "telephony_minutes": 3.0,
        "rag_requests": 2.0,
        "mcp_tool_calls": 3.0,
        "infra_minutes": 3.0,
    }


def test_missing_usage_facts_produce_explicit_zero_components():
    result = _pricebook().calculate(VoiceUsage())

    assert result.cost.total_cost == 0.0
    assert all(
        result.cost.model_dump(mode="json")[field] == 0.0
        for field in (
            "stt_cost",
            "llm_cost",
            "tts_cost",
            "telephony_cost",
            "rag_cost",
            "mcp_tool_cost",
            "infra_cost",
        )
    )


def test_second_based_tts_and_outbound_telephony_use_selected_rates():
    pricebook = PriceBook(
        version="seconds-v1",
        tts_per_second=0.005,
        telephony_outbound_per_minute=0.02,
    )

    result = pricebook.calculate(
        VoiceUsage(
            tts_audio_ms=2_500,
            telephony_minutes=2.0,
            telephony_direction=TelephonyDirection.OUTBOUND,
        )
    )

    assert result.cost.tts_cost == pytest.approx(0.0125)
    assert result.cost.telephony_cost == pytest.approx(0.04)


def test_stt_and_tts_can_independently_determine_billable_minutes():
    pricebook = PriceBook(version="duration-v1")

    stt = pricebook.calculate(VoiceUsage(stt_audio_ms=120_000))
    tts = pricebook.calculate(VoiceUsage(tts_audio_ms=2_500))

    assert stt.cost.billable_audio_minutes == pytest.approx(2.0)
    assert tts.cost.billable_audio_minutes == pytest.approx(2.5 / 60.0)


def test_cost_event_wire_carries_pricebook_and_rejects_invalid_attribution():
    result = _pricebook().calculate(VoiceUsage())
    event = CostEvent(
        event_id=str(uuid.uuid4()),
        session_id="priced-session",
        emitted_at_ms=1,
        cost=result.cost,
        pricebook_version=_pricebook().version,
        attribution=result.attribution,
    )

    wire = event.to_wire()

    assert wire["pricebook_version"] == "voice-2026-07"
    assert wire["attribution"]["stt_audio_minutes"] == 0.0
    with pytest.raises(ValidationError):
        CostEvent.model_validate(
            {**event.model_dump(), "attribution": {"stt_audio_minutes": -1.0}}
        )


def test_pricebook_metadata_is_redacted_before_export():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    result = _pricebook().calculate(VoiceUsage())

    tracer.cost(
        session_id="priced-session",
        cost=result.cost,
        pricebook_version="api_key=do-not-export",
        attribution={**result.attribution, "api_key=do-not-export": 1.0},
    )
    tracer.flush()

    event = exporter.events[0]
    assert event.pricebook_version == "api_key=[REDACTED]"
    assert "api_key=[REDACTED]" in event.attribution


def _tool() -> ToolDef:
    return ToolDef(
        server="crm",
        name="book_meeting",
        description="Book a meeting.",
        json_schema={},
        profile=ToolProfile(expected_latency_ms=10, deadline_ms=1000),
    )


async def _run_session_cost(
    pricebook: PriceBook,
    *,
    direction: TelephonyDirection = TelephonyDirection.INBOUND,
    turns: list[ScriptedLlmTurn] | None = None,
    tools: tuple[ToolDef, ...] = (),
    executor: McpToolExecutor | None = None,
    rag: SpeculativeRagNode | None = None,
    speculation: SpeculationSettings | None = None,
    barge_in_turns: tuple[int, ...] = (),
    provider_attribution: dict[CostComponent, ProviderIdentity] | None = None,
    registry: ModelRegistry | None = None,
    llm_provider: str = TEST_LLM_PROVIDER,
    llm_model: str = TEST_LLM_MODEL,
    cpaas_provider: CpaasTransportMode | None = None,
    telephony_country_code: str | None = None,
):
    budgets = LatencyBudgets()
    clock = ManualClock()
    llm = LocalLlmSimulator(
        turns
        or [
            ScriptedLlmTurn(
                tokens=["Pricing works."],
                usage=UsageReport(
                    prompt_tokens=1_000,
                    cached_prompt_tokens=500,
                    completion_tokens=2_000,
                ),
            )
        ],
        clock,
        token_interval_ms=0,
    )
    resolved_registry = registry or default_model_registry()
    driver = CascadedTurnDriver(
        llm,
        resolved_registry,
        llm_provider,
        llm_model,
        clock,
        budgets,
        min_flush_chars=1,
        tools=tools,
        tool_executor=executor,
    )
    exporter = InMemoryTraceExporter()
    ids = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, "pricing-%d" % index)) for index in range(20)
    )
    tracer = Tracer(
        exporters=[exporter],
        clock=lambda: int(clock.monotonic() * 1000),
        id_factory=lambda: next(ids),
    )
    scenario = SyntheticCallScenario(
        name="priced-call",
        objective="produce complete voice usage",
        turns=[SyntheticTurn(speaker="caller", text="price this call")],
        expected_outcome="answered",
    )

    result = await ConversationHarness("priced-session").run(
        scenario,
        None,
        driver=driver,
        clock=clock,
        tracer=tracer,
        budgets=budgets,
        pricebook=pricebook,
        telephony_direction=direction,
        rag=rag,
        speculation=speculation,
        barge_in_turns=barge_in_turns,
        provider_attribution=provider_attribution,
        provider_registry=resolved_registry,
        cpaas_provider=cpaas_provider,
        telephony_country_code=telephony_country_code,
    )
    tracer.flush()
    costs = [event for event in exporter.events if event.type == "cost"]
    assert len(costs) == 1
    return costs[0], result


async def test_voice_session_emits_one_full_session_cost_event():
    budgets = LatencyBudgets()
    event, _ = await _run_session_cost(_pricebook())
    speech_ms = 3 * budgets.gateway_pacing_ms
    tts_audio_ms = 2 * budgets.gateway_pacing_ms
    call_ms = speech_ms + budgets.stt_final_ms + 3 * budgets.gateway_pacing_ms
    call_minutes = call_ms / 60_000.0

    assert event.turn_id is None
    assert event.pricebook_version == "voice-2026-07"
    assert event.cost.llm_cost == pytest.approx(0.066)
    assert event.cost.stt_cost == pytest.approx(speech_ms / 60_000 * 0.006)
    assert event.cost.tts_cost == pytest.approx(len("Pricing works.") / 1000 * 0.02)
    assert event.cost.telephony_cost == pytest.approx(call_minutes * 0.015)
    assert event.cost.rag_cost == 0.0
    assert event.cost.mcp_tool_cost == 0.0
    assert event.cost.infra_cost == pytest.approx(call_minutes * 0.004)
    assert event.cost.billable_audio_minutes == pytest.approx(call_minutes)
    assert event.attribution["stt_audio_minutes"] == pytest.approx(speech_ms / 60_000)
    assert event.attribution["tts_characters"] == len("Pricing works.")
    assert event.attribution["tts_audio_seconds"] == pytest.approx(tts_audio_ms / 1000)
    assert event.attribution["telephony_minutes"] == pytest.approx(call_minutes)
    assert event.attribution["llm_cached_prompt_tokens"] == 500


async def test_cpaas_voice_session_emits_redacted_cost_dimensions():
    event, _ = await _run_session_cost(
        _pricebook(),
        direction=TelephonyDirection.OUTBOUND,
        cpaas_provider=CpaasTransportMode.TELNYX,
        telephony_country_code="ES",
    )

    assert event.tags == {
        "telephony.direction": "outbound",
        "telephony.provider": "cpaas/telnyx",
        "telephony.country_code": "ES",
        "telephony.billable_seconds": str(event.cost.billable_audio_minutes * 60)
        .rstrip("0")
        .rstrip("."),
        "telephony.cost_component": "telephony_cost",
    }
    assert all("+34" not in value for value in event.tags.values())


async def test_cascaded_session_attributes_stt_llm_and_tts_independently():
    registry = _fixture_registry()
    event, _ = await _run_session_cost(
        _pricebook(),
        provider_attribution={
            CostComponent.STT_COST: ProviderIdentity(
                provider=FIXTURE_STT_PROVIDER, model=FIXTURE_STT_MODEL
            ),
            CostComponent.TTS_COST: ProviderIdentity(
                provider=FIXTURE_TTS_PROVIDER, model=FIXTURE_TTS_MODEL
            ),
        },
        registry=registry,
        llm_provider=FIXTURE_LLM_PROVIDER,
        llm_model=FIXTURE_LLM_MODEL,
    )

    assert event.provider_attribution == {
        CostComponent.STT_COST: ProviderIdentity(
            provider=FIXTURE_STT_PROVIDER, model=FIXTURE_STT_MODEL
        ),
        CostComponent.LLM_COST: ProviderIdentity(
            provider=FIXTURE_LLM_PROVIDER, model=FIXTURE_LLM_MODEL
        ),
        CostComponent.TTS_COST: ProviderIdentity(
            provider=FIXTURE_TTS_PROVIDER, model=FIXTURE_TTS_MODEL
        ),
    }


def test_voice_session_rejects_provider_attribution_conflicting_with_driver():
    clock = ManualClock()
    driver = CascadedTurnDriver(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        default_model_registry(),
        TEST_LLM_PROVIDER,
        TEST_LLM_MODEL,
        clock,
        LatencyBudgets(),
    )
    scenario = SyntheticCallScenario(
        name="provider-conflict",
        objective="reject conflicting ownership",
        turns=[],
        expected_outcome="rejected",
    )

    with pytest.raises(ValueError, match="conflicts for llm_cost"):
        VoiceSession(
            "provider-conflict",
            LocalGatewaySimulator(scenario, clock),
            driver=driver,
            clock=clock,
            provider_attribution={
                CostComponent.LLM_COST: ProviderIdentity(
                    provider=TEST_REALTIME_PROVIDER,
                    model=TEST_REALTIME_MODEL,
                )
            },
            provider_registry=default_model_registry(),
        )


def test_voice_session_rejects_unresolved_media_provider_attribution():
    clock = ManualClock()

    async def responder(text: str) -> str:
        return text

    scenario = SyntheticCallScenario(
        name="provider-unresolved",
        objective="reject unresolved ownership",
        turns=[],
        expected_outcome="rejected",
    )

    with pytest.raises(ValueError, match="absent from the model registry"):
        VoiceSession(
            "provider-unresolved",
            LocalGatewaySimulator(scenario, clock),
            responder=responder,
            clock=clock,
            provider_attribution={
                CostComponent.STT_COST: ProviderIdentity(
                    provider="unregistered-stt",
                    model="model-v1",
                )
            },
            provider_registry=_fixture_registry(),
        )


def test_voice_session_requires_registry_for_media_provider_attribution():
    clock = ManualClock()

    async def responder(text: str) -> str:
        return text

    scenario = SyntheticCallScenario(
        name="provider-registry-required",
        objective="reject attribution without a registry",
        turns=[],
        expected_outcome="rejected",
    )

    with pytest.raises(ValueError, match="requires a model registry"):
        VoiceSession(
            "provider-registry-required",
            LocalGatewaySimulator(scenario, clock),
            responder=responder,
            clock=clock,
            provider_attribution={
                CostComponent.STT_COST: ProviderIdentity(
                    provider=FIXTURE_STT_PROVIDER,
                    model=FIXTURE_STT_MODEL,
                )
            },
        )


@pytest.mark.parametrize(
    ("component", "provider", "model", "expected_capability"),
    [
        (
            CostComponent.STT_COST,
            FIXTURE_TTS_PROVIDER,
            FIXTURE_TTS_MODEL,
            Capability.STT,
        ),
        (
            CostComponent.TTS_COST,
            FIXTURE_STT_PROVIDER,
            FIXTURE_STT_MODEL,
            Capability.TTS,
        ),
    ],
)
def test_voice_session_rejects_media_provider_without_required_capability(
    component, provider, model, expected_capability
):
    clock = ManualClock()

    async def responder(text: str) -> str:
        return text

    scenario = SyntheticCallScenario(
        name="provider-capability-mismatch",
        objective="reject attribution with the wrong media capability",
        turns=[],
        expected_outcome="rejected",
    )

    expected_error = f"lacks {expected_capability.value} capability"
    with pytest.raises(ValueError, match=expected_error):
        VoiceSession(
            "provider-capability-mismatch",
            LocalGatewaySimulator(scenario, clock),
            responder=responder,
            clock=clock,
            provider_attribution={
                component: ProviderIdentity(provider=provider, model=model)
            },
            provider_registry=_fixture_registry(),
        )


def test_voice_session_revalidates_driver_provider_capability():
    clock = ManualClock()
    registry = _fixture_registry()
    driver = CascadedTurnDriver(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        registry,
        FIXTURE_LLM_PROVIDER,
        FIXTURE_LLM_MODEL,
        clock,
        LatencyBudgets(),
    )
    driver.provider_attribution = {
        CostComponent.LLM_COST: ProviderIdentity(
            provider=FIXTURE_STT_PROVIDER,
            model=FIXTURE_STT_MODEL,
        )
    }
    scenario = SyntheticCallScenario(
        name="driver-provider-capability-mismatch",
        objective="reject mutated driver attribution",
        turns=[],
        expected_outcome="rejected",
    )

    with pytest.raises(ValueError, match="lacks llm capability"):
        VoiceSession(
            "driver-provider-capability-mismatch",
            LocalGatewaySimulator(scenario, clock),
            driver=driver,
            clock=clock,
            provider_registry=registry,
        )


def test_voice_session_uses_driver_registry_for_driver_attribution():
    clock = ManualClock()
    driver_registry = _fixture_registry()
    driver = CascadedTurnDriver(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        driver_registry,
        FIXTURE_LLM_PROVIDER,
        FIXTURE_LLM_MODEL,
        clock,
        LatencyBudgets(),
    )
    driver.provider_attribution = {
        CostComponent.LLM_COST: ProviderIdentity(
            provider=FIXTURE_STT_PROVIDER,
            model=FIXTURE_STT_MODEL,
        )
    }
    supplied_registry = _fixture_registry()
    supplied_model = supplied_registry.get(FIXTURE_STT_PROVIDER, FIXTURE_STT_MODEL)
    assert supplied_model is not None
    supplied_model.capabilities.append(Capability.LLM)
    scenario = SyntheticCallScenario(
        name="driver-registry-authority",
        objective="retain driver registry authority",
        turns=[],
        expected_outcome="rejected",
    )

    with pytest.raises(ValueError, match="lacks llm capability"):
        VoiceSession(
            "driver-registry-authority",
            LocalGatewaySimulator(scenario, clock),
            driver=driver,
            clock=clock,
            provider_registry=supplied_registry,
        )


async def test_responder_session_does_not_invent_provider_attribution():
    clock = ManualClock()

    async def responder(text: str) -> str:
        return f"heard {text}"

    scenario = SyntheticCallScenario(
        name="local-responder-attribution",
        objective="leave unknown provider ownership absent",
        turns=[SyntheticTurn(speaker="caller", text="hello")],
        expected_outcome="answered",
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(
        exporters=[exporter],
        clock=lambda: int(clock.monotonic() * 1000),
    )

    await VoiceSession(
        "local-responder-attribution",
        LocalGatewaySimulator(scenario, clock),
        responder=responder,
        clock=clock,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()

    event = next(item for item in exporter.events if item.type == "cost")
    assert event.provider_attribution == {}


async def test_realtime_session_attributes_speech_components_to_one_provider():
    clock = ManualClock()
    registry = _fixture_registry()
    realtime = LocalRealtimeSimulator(
        [
            ScriptedRealtimeTurn(
                user_text="price this call",
                assistant_text="Realtime pricing works.",
                usage=UsageReport(prompt_tokens=1_000, completion_tokens=2_000),
            )
        ],
        clock,
        transcript_interval_ms=0,
    )
    driver = RealtimeTurnDriver(
        realtime,
        RealtimeSessionConfig(
            provider=FIXTURE_REALTIME_PROVIDER,
            model=FIXTURE_REALTIME_MODEL,
            system_prompt="Answer the caller.",
        ),
        registry,
        clock,
        LatencyBudgets(),
    )
    exporter = InMemoryTraceExporter()
    ids = (
        str(uuid.uuid5(uuid.NAMESPACE_URL, "realtime-pricing-%d" % index))
        for index in range(20)
    )
    tracer = Tracer(
        exporters=[exporter],
        clock=lambda: int(clock.monotonic() * 1000),
        id_factory=lambda: next(ids),
    )
    scenario = SyntheticCallScenario(
        name="realtime-priced-call",
        objective="attribute realtime voice costs",
        turns=[SyntheticTurn(speaker="caller", text="price this call")],
        expected_outcome="answered",
    )

    await ConversationHarness("realtime-priced-session").run(
        scenario,
        driver=driver,
        clock=clock,
        tracer=tracer,
        budgets=LatencyBudgets(),
        pricebook=_pricebook(),
    )
    tracer.flush()

    event = next(item for item in exporter.events if item.type == "cost")
    identity = ProviderIdentity(
        provider=FIXTURE_REALTIME_PROVIDER,
        model=FIXTURE_REALTIME_MODEL,
    )
    assert event.provider_attribution == {
        CostComponent.STT_COST: identity,
        CostComponent.LLM_COST: identity,
        CostComponent.TTS_COST: identity,
    }


async def test_voice_session_propagates_outbound_and_second_based_tts_pricing():
    pricebook = PriceBook(
        version="outbound-seconds",
        tts_per_second=0.005,
        telephony_outbound_per_minute=0.02,
    )
    budgets = LatencyBudgets()
    event, _ = await _run_session_cost(pricebook, direction=TelephonyDirection.OUTBOUND)
    tts_audio_seconds = 2 * budgets.gateway_pacing_ms / 1000
    call_minutes = (6 * budgets.gateway_pacing_ms + budgets.stt_final_ms) / 60_000

    assert event.cost.tts_cost == pytest.approx(tts_audio_seconds * 0.005)
    assert event.cost.telephony_cost == pytest.approx(call_minutes * 0.02)
    assert event.attribution["tts_audio_seconds"] == pytest.approx(tts_audio_seconds)


async def test_barge_in_prices_only_played_tts_duration():
    pricebook = PriceBook(version="interrupted-seconds", tts_per_second=1.0)
    budgets = LatencyBudgets()

    event, result = await _run_session_cost(pricebook, barge_in_turns=(0,))

    assert result.turn_records[0].interrupted is True
    expected_seconds = 2 * budgets.gateway_pacing_ms / 1000
    assert event.attribution["tts_audio_seconds"] == pytest.approx(expected_seconds)
    assert event.cost.tts_cost == pytest.approx(expected_seconds)
    assert event.provider_attribution == {
        CostComponent.LLM_COST: ProviderIdentity(
            provider=TEST_LLM_PROVIDER,
            model=TEST_LLM_MODEL,
        )
    }


async def test_voice_session_prices_rag_mcp_and_accumulated_llm_usage():
    clock = ManualClock()
    tool = _tool()
    executor = McpToolExecutor(
        McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key]), clock
    )
    call = ToolCallReady(call_id="call-1", name=tool.name, arguments={"day": "Tuesday"})
    turns = [
        ScriptedLlmTurn(
            tokens=[],
            usage=UsageReport(400, 0, cached_prompt_tokens=200),
            finish_reason="tool_calls",
            tool_calls=[call],
        ),
        ScriptedLlmTurn(
            tokens=["Done."],
            usage=UsageReport(600, 2_000, cached_prompt_tokens=300),
        ),
    ]
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [RagChunk(id="price", source="pricing", text="Price this call.")]
        )
    )

    event, _ = await _run_session_cost(
        _pricebook(),
        turns=turns,
        tools=(tool,),
        executor=executor,
        rag=rag,
        speculation=SpeculationSettings(enabled_rag_prefetch=False),
    )

    assert event.attribution["llm_prompt_tokens"] == 1_000
    assert event.attribution["llm_cached_prompt_tokens"] == 500
    assert event.attribution["llm_completion_tokens"] == 2_000
    assert event.attribution["rag_requests"] == 1
    assert event.attribution["mcp_tool_calls"] == 1
    assert event.cost.llm_cost == pytest.approx(0.066)
    assert event.cost.rag_cost == pytest.approx(0.001)
    assert event.cost.mcp_tool_cost == pytest.approx(0.002)


async def test_schema_rejected_mcp_call_is_not_billable():
    clock = ManualClock()
    tool = _tool()
    executor = McpToolExecutor(
        McpClient(
            LocalMcpCommandTransport(),
            allowed_tools=[tool.key],
            tool_schemas={tool.key: McpToolSchema(required_fields={"day": str})},
        ),
        clock,
    )
    call = ToolCallReady(call_id="schema-call", name=tool.name, arguments={})

    event, _ = await _run_session_cost(
        _pricebook(),
        turns=[
            ScriptedLlmTurn(
                tokens=[],
                usage=UsageReport(1, 0),
                finish_reason="tool_calls",
                tool_calls=[call],
            ),
            ScriptedLlmTurn(tokens=["Need a day."], usage=UsageReport(1, 3)),
        ],
        tools=(tool,),
        executor=executor,
    )

    assert event.attribution["mcp_tool_calls"] == 0
    assert event.cost.mcp_tool_cost == 0
    assert event.provider_attribution == {
        CostComponent.LLM_COST: ProviderIdentity(
            provider=TEST_LLM_PROVIDER,
            model=TEST_LLM_MODEL,
        )
    }


async def test_graph_driver_propagates_rag_and_mcp_cost_facts():
    clock = ManualClock()
    tool = _tool()
    executor = McpToolExecutor(
        McpClient(LocalMcpCommandTransport(), allowed_tools=[tool.key]), clock
    )
    call = ToolCallReady(call_id="graph-call", name=tool.name, arguments={})
    inner = CascadedTurnDriver(
        LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=[],
                    usage=UsageReport(1, 0),
                    finish_reason="tool_calls",
                    tool_calls=[call],
                ),
                ScriptedLlmTurn(tokens=["Booked."], usage=UsageReport(2, 1)),
            ],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        TEST_LLM_PROVIDER,
        TEST_LLM_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
        tools=(tool,),
        tool_executor=executor,
    )
    inner.provider_attribution = MappingProxyType(inner.provider_attribution)  # type: ignore[assignment]
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [RagChunk(id="graph", source="booking", text="Book this call.")]
        )
    )
    driver = GraphTurnDriver(
        default_agent_graph(inner, rag=rag).compile(),
        session_id="graph-priced",
        clock=clock,
    )
    scenario = SyntheticCallScenario(
        name="graph-priced",
        objective="propagate graph usage",
        turns=[SyntheticTurn(speaker="caller", text="book this call")],
        expected_outcome="answered",
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    await VoiceSession(
        "graph-priced",
        LocalGatewaySimulator(scenario, clock),
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    assert cost.attribution["rag_requests"] == 1
    assert cost.attribution["mcp_tool_calls"] == 1
    assert cost.cost.rag_cost == pytest.approx(0.001)
    assert cost.cost.mcp_tool_cost == pytest.approx(0.002)
    assert cost.provider_attribution == {
        CostComponent.LLM_COST: ProviderIdentity(
            provider=TEST_LLM_PROVIDER,
            model=TEST_LLM_MODEL,
        )
    }


class _BlockingRagIndex:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def retrieve(self, query: str, max_chunks: int = 8):
        del query, max_chunks
        self.started.set()
        await asyncio.Event().wait()


class _GraphRagInterruptGateway:
    def __init__(self, rag_started: asyncio.Event) -> None:
        self.rag_started = rag_started
        self.sent: list[ControlEvent] = []

    async def send(self, envelope, payload) -> None:
        self.sent.append(ControlEvent(envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="graph-rag", seq=0, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="graph-rag",
                turn_id="turn-1",
                seq=1,
                ts_ms=10,
            ),
            SttFinal(text="book", provider="local", stt_ms=10),
        )
        await self.rag_started.wait()
        yield ControlEvent(
            Envelope(
                type="vad.speech_start",
                session_id="graph-rag",
                turn_id="interrupt",
                seq=2,
                ts_ms=20,
            ),
            VadSpeechStart(at_ms=20),
        )
        assert any(isinstance(event.payload, TtsStreamEnd) for event in self.sent)
        yield ControlEvent(
            Envelope(type="session.ended", session_id="graph-rag", seq=3, ts_ms=30),
            SessionEnded(reason="done"),
        )


async def test_cancelled_graph_rag_dispatch_remains_billable():
    clock = ManualClock()
    inner = CascadedTurnDriver(
        LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["Unused."], usage=UsageReport(1, 1))],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        TEST_LLM_PROVIDER,
        TEST_LLM_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    rag_index = _BlockingRagIndex()
    rag = SpeculativeRagNode(rag_index, deadline_ms=20_000)
    driver = GraphTurnDriver(
        default_agent_graph(inner, rag=rag).compile(),
        session_id="cancelled-graph-rag",
        clock=clock,
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    await VoiceSession(
        "cancelled-graph-rag",
        _GraphRagInterruptGateway(rag_index.started),
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    assert cost.attribution["rag_requests"] == 1
    assert cost.cost.rag_cost == pytest.approx(0.001)


class _BlockingGraphToolTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def call_tool(self, server, tool, arguments):
        self.started.set()
        await asyncio.Event().wait()


class _GraphToolInterruptGateway:
    def __init__(self, tool_started: asyncio.Event) -> None:
        self.tool_started = tool_started
        self.sent: list[ControlEvent] = []

    async def send(self, envelope, payload) -> None:
        self.sent.append(ControlEvent(envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="graph-tool", seq=0, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="graph-tool",
                turn_id="turn-1",
                seq=1,
                ts_ms=10,
            ),
            SttFinal(text="book", provider="local", stt_ms=10),
        )
        await self.tool_started.wait()
        yield ControlEvent(
            Envelope(
                type="vad.speech_start",
                session_id="graph-tool",
                turn_id="interrupt",
                seq=2,
                ts_ms=20,
            ),
            VadSpeechStart(at_ms=20),
        )
        assert any(isinstance(event.payload, TtsStreamEnd) for event in self.sent)
        yield ControlEvent(
            Envelope(type="session.ended", session_id="graph-tool", seq=3, ts_ms=30),
            SessionEnded(reason="done"),
        )


async def test_cancelled_graph_mcp_dispatch_remains_billable():
    clock = ManualClock()
    transport = _BlockingGraphToolTransport()
    tool = _tool()
    node = McpToolNode(
        tool,
        McpToolExecutor(McpClient(transport, allowed_tools=[tool.key]), clock),
        lambda _state: {},
    )
    graph = (
        AgentGraph()
        .add_node("tool", node)
        .add_node("say", SayNode("Booked."))
        .add_edge("tool", "say")
        .set_entry("tool")
        .compile()
    )
    driver = GraphTurnDriver(
        graph,
        session_id="graph-tool",
        clock=clock,
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    await VoiceSession(
        "graph-tool",
        _GraphToolInterruptGateway(transport.started),
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    assert cost.attribution["mcp_tool_calls"] == 1
    assert cost.cost.mcp_tool_cost == pytest.approx(0.002)


async def test_cancelled_rag_dispatch_remains_billable():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    rag = SpeculativeRagNode(
        InMemoryRagIndex(
            [RagChunk(id="slow", source="policy", text="Slow evidence")],
            delay_ms=10_000,
        )
    )

    async def responder(_text: str) -> str:
        return "unused"

    session = VoiceSession(
        "cancelled-rag",
        object(),
        responder,
        tracer=tracer,
        rag=rag,
        pricebook=_pricebook(),
    )
    task = asyncio.create_task(session._priced_rag_prefetch("revised query"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    session._emit_session_cost(0, 1)
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")
    assert cost.attribution["rag_requests"] == 1
    assert cost.cost.rag_cost == pytest.approx(0.001)


class _DuplicateVadEndGateway(LocalGatewaySimulator):
    async def events(self):
        async for event in super().events():
            yield event
            if isinstance(event.payload, VadSpeechEnd):
                yield event


class _VadFactGateway:
    def __init__(self, events: list[ControlEvent]) -> None:
        self._events = events

    async def send(self, envelope, payload) -> None:
        del envelope, payload

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="vad", seq=0, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        for event in self._events:
            yield event
        yield ControlEvent(
            Envelope(type="session.ended", session_id="vad", seq=99, ts_ms=10),
            SessionEnded(reason="done"),
        )


class _OpenVadFloodGateway:
    def __init__(self, count: int) -> None:
        self.count = count
        self.flooded = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, envelope, payload) -> None:
        del envelope, payload

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="flood", seq=0, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        for index in range(self.count):
            turn_id = "fake-%d" % index
            started_at = index * 2 + 1
            yield ControlEvent(
                Envelope(
                    type="vad.speech_start",
                    session_id="flood",
                    turn_id=turn_id,
                    seq=started_at,
                    ts_ms=started_at,
                ),
                VadSpeechStart(at_ms=started_at),
            )
            yield ControlEvent(
                Envelope(
                    type="vad.speech_end",
                    session_id="flood",
                    turn_id=turn_id,
                    seq=started_at + 1,
                    ts_ms=started_at + 1,
                ),
                VadSpeechEnd(at_ms=started_at + 1, speech_ms=1),
            )
        self.flooded.set()
        await self.release.wait()
        yield ControlEvent(
            Envelope(
                type="session.ended",
                session_id="flood",
                seq=self.count * 2 + 1,
                ts_ms=self.count * 2 + 1,
            ),
            SessionEnded(reason="done"),
        )


class _PerUtterancePlaybackGateway(LocalGatewaySimulator):
    async def _agent_response(self, turn_id: str, *, interrupt: bool):
        assert not interrupt
        utterances = await self._collect_turn_directives(turn_id)
        for utterance in utterances:
            yield self._playback(turn_id, utterance.utterance_id, "started", 0)
            yield self._playback(
                turn_id, utterance.utterance_id, "mark", len(utterance.text)
            )
            yield self._playback(
                turn_id, utterance.utterance_id, "finished", len(utterance.text)
            )


async def _run_vad_accounting(gateway_type):
    clock = ManualClock()
    scenario = SyntheticCallScenario(
        name="vad-accounting",
        objective="account caller speech once",
        turns=[SyntheticTurn(speaker="caller", text="hello there")],
        expected_outcome="answered",
    )
    gateway = gateway_type(scenario, clock)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(_text: str) -> str:
        return "Hello."

    await VoiceSession(
        "vad-priced-session",
        gateway,
        responder,
        clock=clock,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")
    return cost, tracer


async def test_duplicate_vad_end_cannot_inflate_stt_cost():
    baseline, _ = await _run_vad_accounting(LocalGatewaySimulator)
    duplicated, tracer = await _run_vad_accounting(_DuplicateVadEndGateway)

    assert (
        duplicated.attribution["stt_audio_minutes"]
        == baseline.attribution["stt_audio_minutes"]
    )
    assert duplicated.cost.stt_cost == baseline.cost.stt_cost
    assert tracer.dropped_events == 1


async def test_fabricated_or_replayed_vad_cycles_cannot_create_stt_cost():
    events = [
        ControlEvent(
            Envelope(
                type="vad.speech_start",
                session_id="vad",
                turn_id="fake",
                seq=1,
                ts_ms=1,
            ),
            VadSpeechStart(at_ms=1),
        ),
        ControlEvent(
            Envelope(
                type="vad.speech_end", session_id="vad", turn_id="fake", seq=2, ts_ms=2
            ),
            VadSpeechEnd(at_ms=2, speech_ms=1),
        ),
        ControlEvent(
            Envelope(
                type="vad.speech_start",
                session_id="vad",
                turn_id="fake",
                seq=3,
                ts_ms=3,
            ),
            VadSpeechStart(at_ms=3),
        ),
        ControlEvent(
            Envelope(
                type="vad.speech_end", session_id="vad", turn_id="fake", seq=4, ts_ms=4
            ),
            VadSpeechEnd(at_ms=4, speech_ms=1),
        ),
    ]
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(_text: str) -> str:
        return "unused"

    await VoiceSession(
        "vad",
        _VadFactGateway(events),
        responder,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    assert cost.attribution["stt_audio_minutes"] == 0
    assert cost.cost.stt_cost == 0
    assert tracer.dropped_events == 3


async def test_open_session_vad_flood_retains_only_one_pending_cycle():
    gateway = _OpenVadFloodGateway(100)
    tracer = Tracer(exporters=[])

    async def responder(_text: str) -> str:
        return "unused"

    session = VoiceSession(
        "flood",
        gateway,
        responder,
        tracer=tracer,
        pricebook=_pricebook(),
    )
    task = asyncio.create_task(session.run())
    await gateway.flooded.wait()

    assert len(session._vad_started_at) == 0
    assert len(session._pending_stt_speech_ms) == 1
    assert len(session._stt_billed_turns) == 0
    assert tracer.dropped_events == 198

    gateway.release.set()
    await task


async def test_inconsistent_vad_duration_is_dropped():
    events = [
        ControlEvent(
            Envelope(
                type="vad.speech_start", session_id="vad", turn_id="bad", seq=1, ts_ms=1
            ),
            VadSpeechStart(at_ms=1),
        ),
        ControlEvent(
            Envelope(
                type="vad.speech_end", session_id="vad", turn_id="bad", seq=2, ts_ms=3
            ),
            VadSpeechEnd(at_ms=3, speech_ms=99),
        ),
    ]
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(_text: str) -> str:
        return "unused"

    await VoiceSession(
        "vad",
        _VadFactGateway(events),
        responder,
        tracer=tracer,
        pricebook=_pricebook(),
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    assert cost.attribution["stt_audio_minutes"] == 0
    assert tracer.dropped_events == 1


def test_extreme_session_duration_is_dropped_fail_open():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(_text: str) -> str:
        return "unused"

    session = VoiceSession(
        "extreme-duration",
        object(),
        responder,
        tracer=tracer,
        pricebook=_pricebook(),
    )

    session._emit_session_cost(0, MAX_CONTROL_TIMESTAMP_MS)
    tracer.flush()

    assert exporter.events == []
    assert tracer.dropped_events == 1


async def test_tts_duration_accumulates_each_playback_interval():
    clock = ManualClock()
    budgets = LatencyBudgets()
    scenario = SyntheticCallScenario(
        name="per-utterance-playback",
        objective="account every synthesized clause",
        turns=[SyntheticTurn(speaker="caller", text="say two clauses")],
        expected_outcome="answered",
    )
    driver = CascadedTurnDriver(
        LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=["First clause. ", "Second clause."],
                    usage=UsageReport(1, 2),
                )
            ],
            clock,
            token_interval_ms=0,
        ),
        default_model_registry(),
        TEST_LLM_PROVIDER,
        TEST_LLM_MODEL,
        clock,
        budgets,
        min_flush_chars=1,
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    pricebook = PriceBook(version="tts-seconds", tts_per_second=1.0)

    await VoiceSession(
        "per-utterance-session",
        _PerUtterancePlaybackGateway(scenario, clock, budgets=budgets),
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=pricebook,
    ).run()
    tracer.flush()
    cost = next(event for event in exporter.events if event.type == "cost")

    expected_ms = 4 * budgets.gateway_pacing_ms
    assert cost.attribution["tts_audio_seconds"] == pytest.approx(expected_ms / 1000)
    assert cost.cost.tts_cost == pytest.approx(expected_ms / 1000)


async def test_voice_session_uses_process_global_tracer_for_costs():
    clock = ManualClock()
    scenario = SyntheticCallScenario(
        name="global-tracer",
        objective="emit through configured telemetry",
        turns=[SyntheticTurn(speaker="caller", text="hello")],
        expected_outcome="answered",
    )
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    observe_module.set_tracer(tracer)

    async def responder(_text: str) -> str:
        return "Hello."

    try:
        await VoiceSession(
            "global-tracer-session",
            LocalGatewaySimulator(scenario, clock),
            responder,
            clock=clock,
            pricebook=_pricebook(),
        ).run()
        tracer.flush()
        assert any(event.type == "cost" for event in exporter.events)
    finally:
        observe_module.set_tracer(None)
