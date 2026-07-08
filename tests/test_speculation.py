import asyncio

import pytest

from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver
from lucy.llm import (
    LlmMessage,
    LlmRequest,
    LocalLlmSimulator,
    OpenAiCompatibleAdapter,
    PROMPT_CACHE_FIELD,
    ScriptedLlmTurn,
    ToolCallReady,
    UsageReport,
    compute_cache_key,
)
from lucy.mcp import McpClient
from lucy.providers import default_model_registry
from lucy.rag import InMemoryRagIndex, RagChunk, SpeculativeRagNode
from lucy.session import (
    SpeculationController,
    SpeculativeAction,
    TurnContext,
    VoiceSession,
)
from lucy.settings import LatencyBudgets, SpeculationSettings
from lucy.testing import LocalMcpCommandTransport
from lucy.tools import McpToolExecutor, ToolDef, ToolProfile
from lucy.transport.schema import (
    ControlEvent,
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    TtsPlayback,
    TtsSpeak,
)


def test_speculative_llm_is_off_by_default():
    settings = SpeculationSettings()

    assert settings.enabled_llm_start is False
    assert settings.enabled_rag_prefetch is True


def test_env_overrides_speculation_settings(monkeypatch):
    monkeypatch.setenv("LUCY_SPECULATION_ENABLED_LLM_START", "1")
    monkeypatch.setenv("LUCY_SPECULATION_LLM_START_STABILITY", "0.8")

    settings = SpeculationSettings()

    assert settings.enabled_llm_start is True
    assert settings.llm_start_stability == 0.8


def test_threshold_ordering_validated():
    with pytest.raises(ValueError):
        SpeculationSettings(rag_prefetch_stability=0.95, llm_start_stability=0.9)


def test_on_partial_below_thresholds_returns_none():
    controller = SpeculationController(SpeculationSettings())

    assert controller.on_partial("book", 0.1) == SpeculativeAction.NONE


def test_on_partial_at_rag_threshold_prefetches_once_per_text():
    controller = SpeculationController(SpeculationSettings())

    assert controller.on_partial("Book   Demo", 0.6) == SpeculativeAction.PREFETCH_RAG
    assert controller.on_partial("book demo", 0.7) == SpeculativeAction.NONE


def test_on_partial_never_starts_llm_when_gated_off():
    controller = SpeculationController(SpeculationSettings())

    assert controller.on_partial("book demo", 1.0) == SpeculativeAction.PREFETCH_RAG


def test_on_partial_starts_llm_once_per_turn_when_enabled():
    controller = SpeculationController(SpeculationSettings(enabled_llm_start=True))

    assert controller.on_partial("book demo", 0.9) == SpeculativeAction.START_LLM
    assert controller.on_partial("book demo now", 1.0) == SpeculativeAction.PREFETCH_RAG


async def test_prefix_match_promotes_in_flight_run():
    controller = SpeculationController(SpeculationSettings(enabled_llm_start=True))
    task = asyncio.create_task(asyncio.Event().wait())

    controller.start("Book demo", task)
    promoted = await controller.reconcile("book demo Tuesday")

    assert promoted is True
    assert task.cancelled() is False
    task.cancel()


async def test_revision_aborts_run_and_clears_speculation():
    controller = SpeculationController(SpeculationSettings(enabled_llm_start=True))
    task = asyncio.create_task(asyncio.Event().wait())

    controller.start("book demo", task)
    promoted = await controller.reconcile("cancel that")

    assert promoted is False
    assert task.cancelled() is True
    assert controller.speculating is False


def test_cache_key_stable_within_session_distinct_across_sessions():
    assert compute_cache_key("s1", "prompt") == compute_cache_key("s1", "prompt")
    assert compute_cache_key("s1", "prompt") != compute_cache_key("s2", "prompt")


def test_adapter_payload_carries_cache_key_field_only_when_set():
    adapter = OpenAiCompatibleAdapter("http://llm.local")
    request = LlmRequest(
        provider="openai",
        model="gpt-5",
        messages=[LlmMessage(role="user", content="hi")],
    )

    assert PROMPT_CACHE_FIELD not in adapter.build_payload(request)
    with_key = request.model_copy(update={"cache_key": "abc"})
    assert adapter.build_payload(with_key)[PROMPT_CACHE_FIELD] == "abc"


async def test_simulator_records_cache_keys_for_requests():
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1))],
        ManualClock(),
        token_interval_ms=0,
    )
    request = LlmRequest(
        provider="openai",
        model="gpt-5",
        messages=[LlmMessage(role="user", content="hi")],
        cache_key="cache",
    )

    assert [event async for event in sim.stream_chat(request)]
    assert sim.seen_cache_keys == ["cache"]


class PartialFinalGateway:
    def __init__(
        self,
        clock: ManualClock,
        *,
        partial: str = "book demo",
        final: str = "book demo",
        stability: float = 0.95,
        head_start_ms: float = 0.0,
    ) -> None:
        self.clock = clock
        self.partial = partial
        self.final = final
        self.stability = stability
        self.head_start_ms = head_start_ms
        self.sent: list[ControlEvent] = []
        self.sent_before_final: list[ControlEvent] = []
        self.first_speak_after_final_ms: float | None = None
        self._inbound: asyncio.Queue[tuple[Envelope, object]] = asyncio.Queue()
        self._final_clock = 0.0

    async def send(self, envelope: Envelope, payload: object) -> None:
        if isinstance(payload, TtsSpeak) and self.first_speak_after_final_ms is None:
            self.first_speak_after_final_ms = (
                self.clock.monotonic() * 1000 - self._final_clock
            )
        event = ControlEvent(envelope, payload)
        self.sent.append(event)
        await self._inbound.put((envelope, payload))

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="s", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="+10000000000", codecs=["pcmu"]),
        )
        if self.partial:
            yield ControlEvent(
                Envelope(
                    type="stt.partial", session_id="s", turn_id="t0", seq=2, ts_ms=10
                ),
                SttPartial(
                    text=self.partial, stability=self.stability, provider="local"
                ),
            )
            await asyncio.sleep(0)
            self.clock.advance(self.head_start_ms)
            await asyncio.sleep(0)
            self.sent_before_final = list(self.sent)
        self._final_clock = self.clock.monotonic() * 1000
        yield ControlEvent(
            Envelope(type="stt.final", session_id="s", turn_id="t0", seq=3, ts_ms=20),
            SttFinal(text=self.final, provider="local", stt_ms=60),
        )
        await asyncio.sleep(0)
        utterances: list[TtsSpeak] = []
        while True:
            while self._inbound.empty():
                self.clock.advance(10)
                await asyncio.sleep(0)
            _, payload = await self._inbound.get()
            if isinstance(payload, TtsSpeak):
                utterances.append(payload)
            else:
                break
        if utterances:
            yield ControlEvent(
                Envelope(
                    type="tts.playback", session_id="s", turn_id="t0", seq=4, ts_ms=30
                ),
                TtsPlayback(utterance_id=utterances[-1].utterance_id, state="started"),
            )
            yield ControlEvent(
                Envelope(
                    type="tts.playback", session_id="s", turn_id="t0", seq=5, ts_ms=40
                ),
                TtsPlayback(
                    utterance_id=utterances[-1].utterance_id,
                    state="finished",
                    mark_chars=len(utterances[-1].text),
                ),
            )
        yield ControlEvent(
            Envelope(type="session.ended", session_id="s", seq=6, ts_ms=50),
            SessionEnded(reason="done"),
        )


def _driver(clock: ManualClock, sim: LocalLlmSimulator) -> CascadedTurnDriver:
    return CascadedTurnDriver(
        sim,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )


def _rag_node() -> SpeculativeRagNode:
    return SpeculativeRagNode(
        InMemoryRagIndex(
            [
                RagChunk(
                    id="booking",
                    source="booking_policy",
                    text="Demos can be booked on Tuesday morning.",
                )
            ]
        )
    )


async def test_partial_prefetch_makes_final_retrieve_a_cache_hit():
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book demo", final="book demo")
    session = VoiceSession("s", gateway, _answer, clock=clock, rag=_rag_node())

    records = await session.run()

    assert records[0].rag_cache_hit is True


async def test_prefetch_tasks_never_outlive_the_session():
    before = set(asyncio.all_tasks())
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book demo", final="book demo")
    session = VoiceSession("s", gateway, _answer, clock=clock, rag=_rag_node())

    await session.run()

    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def _answer(text: str) -> str:
    return "Answer: %s" % text


async def test_speculative_run_emits_no_tts_speak_before_final():
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book", final="book demo")
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=0,
    )
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=_driver(clock, sim),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    )

    await session.run()

    assert [
        event
        for event in gateway.sent_before_final
        if isinstance(event.payload, TtsSpeak)
    ] == []
    assert any(isinstance(event.payload, TtsSpeak) for event in gateway.sent)


async def test_promotion_flushes_buffered_sentences_in_order():
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book", final="book demo")
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["First. ", "Second."], usage=UsageReport(1, 2))],
        clock,
        token_interval_ms=0,
    )
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=_driver(clock, sim),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    )

    await session.run()

    spoken = [
        event.payload.text
        for event in gateway.sent
        if isinstance(event.payload, TtsSpeak)
    ]
    assert spoken == ["First.", "Second."]


async def test_revision_aborts_run_and_nothing_speculative_is_spoken():
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book", final="cancel")
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["Wrong."], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(tokens=["Right."], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=_driver(clock, sim),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    )

    await session.run()

    spoken = [
        event.payload.text
        for event in gateway.sent
        if isinstance(event.payload, TtsSpeak)
    ]
    assert spoken == ["Right."]


async def test_speculative_tool_call_waits_for_promotion():
    clock = ManualClock()
    call = ToolCallReady(call_id="c1", name="book_meeting", arguments={})
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=[],
                usage=UsageReport(1, 0),
                finish_reason="tool_calls",
                tool_calls=[call],
            ),
            ScriptedLlmTurn(tokens=["Done."], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    transport = LocalMcpCommandTransport()
    tool = ToolDef(
        server="crm",
        name="book_meeting",
        description="",
        json_schema={},
        profile=ToolProfile(expected_latency_ms=10, deadline_ms=100),
    )
    driver = CascadedTurnDriver(
        sim,
        default_model_registry(),
        "openai",
        "gpt-5",
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
        tool_executor=McpToolExecutor(
            McpClient(transport, allowed_tools=[tool.key]), clock
        ),
        tools=[tool],
    )
    context = TurnContext(turn_id="t", speculative=True)
    events: list[object] = []

    async def consume():
        async for event in driver.run_turn("book", [], turn_context=context):
            events.append(event)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    assert transport.commands == []
    context.speculative = False
    context.promoted.set()
    await task

    assert transport.commands[0]["tool"] == "book_meeting"
    assert events


async def test_no_orphan_tasks_after_abort():
    before = set(asyncio.all_tasks())
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book", final="cancel")
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["Wrong."], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(tokens=["Right."], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=_driver(clock, sim),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    )

    await session.run()

    leaked = set(asyncio.all_tasks()) - before
    leaked.discard(asyncio.current_task())
    assert leaked == set()


async def test_simulator_records_cache_keys_for_speculative_and_serial_runs():
    clock = ManualClock()
    gateway = PartialFinalGateway(clock, partial="book", final="cancel")
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["Wrong."], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(tokens=["Right."], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    session = VoiceSession(
        "s",
        gateway,
        None,
        driver=_driver(clock, sim),
        clock=clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    )

    await session.run()

    assert len(set(sim.seen_cache_keys)) == 1
    assert sim.seen_cache_keys[0] is not None


def test_serial_path_fits_latency_budget_defaults():
    budgets = LatencyBudgets()
    component_sum = (
        budgets.endpoint_silence_ms
        + budgets.stt_final_ms
        + budgets.control_transport_ms
        + budgets.graph_dispatch_ms
        + budgets.llm_first_clause_ms
        + budgets.tts_first_byte_ms
        + budgets.gateway_pacing_ms
    )

    assert component_sum == 790
    assert component_sum <= budgets.turn_total_ms


async def test_speculation_shaves_final_to_first_speak_gap():
    budgets = LatencyBudgets()
    head_start_ms = budgets.llm_first_clause_ms / 2

    serial_clock = ManualClock()
    serial_gateway = PartialFinalGateway(serial_clock, partial="", final="book demo")
    serial_sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1))],
        serial_clock,
        token_interval_ms=budgets.llm_first_clause_ms,
    )
    await VoiceSession(
        "serial",
        serial_gateway,
        None,
        driver=_driver(serial_clock, serial_sim),
        clock=serial_clock,
    ).run()

    spec_clock = ManualClock()
    spec_gateway = PartialFinalGateway(
        spec_clock, partial="book", final="book demo", head_start_ms=head_start_ms
    )
    spec_sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hi."], usage=UsageReport(1, 1))],
        spec_clock,
        token_interval_ms=budgets.llm_first_clause_ms,
    )
    await VoiceSession(
        "spec",
        spec_gateway,
        None,
        driver=_driver(spec_clock, spec_sim),
        clock=spec_clock,
        speculation=SpeculationSettings(enabled_llm_start=True),
    ).run()

    assert spec_gateway.first_speak_after_final_ms == pytest.approx(
        serial_gateway.first_speak_after_final_ms - head_start_ms
    )
