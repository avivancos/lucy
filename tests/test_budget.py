import asyncio

import pytest
from pydantic import ValidationError

from lucy.budget import (
    BudgetController,
    BudgetExceeded,
    BudgetLevel,
    BudgetMeteringError,
    BudgetPolicy,
    BudgetScope,
    BudgetedLlmProvider,
)
from lucy.clock import ManualClock
from lucy.drivers import CascadedTurnDriver, TurnDriverReport
from lucy.evals import SyntheticCallScenario, SyntheticTurn
from lucy.llm import (
    LlmMessage,
    LlmRequest,
    LocalLlmSimulator,
    ScriptedLlmTurn,
    UsageReport,
)
from lucy.limits import (
    MAX_BUDGET_IDENTITY_LENGTH,
    MAX_BUDGET_IDENTITIES,
    MAX_BUDGET_RATE_LIMIT,
    MAX_CONTROL_DURATION_MS,
    MAX_SESSION_BACKGROUND_TASKS,
    MAX_USAGE_UNITS,
)
from lucy.observe import Tracer
from lucy.pricing import PriceBook
from lucy.providers import default_model_registry
from lucy.session import VoiceSession, _ActiveTurn
from lucy.settings import LatencyBudgets
from lucy.testing import InMemoryTraceExporter
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import (
    BargeIn,
    ControlEvent,
    Envelope,
    SessionEnd,
    SessionEnded,
    SessionStarted,
    SttFinal,
    TtsSpeak,
    TtsStreamEnd,
)

TEST_PROVIDER = "openai"
TEST_MODEL = "gpt-5"


def _request() -> LlmRequest:
    return LlmRequest(
        provider=TEST_PROVIDER,
        model=TEST_MODEL,
        messages=[LlmMessage(role="user", content="Hello")],
    )


async def _collect(provider: BudgetedLlmProvider) -> list[object]:
    return [event async for event in provider.stream_chat(_request())]


@pytest.mark.parametrize(
    ("soft_field", "hard_field", "message"),
    (
        ("session_soft_usd", "session_hard_usd", "session soft limit"),
        ("agent_soft_usd", "agent_hard_usd", "agent soft limit"),
        (
            "soft_requests_per_minute",
            "hard_requests_per_minute",
            "RPM soft limit",
        ),
        ("soft_tokens_per_minute", "hard_tokens_per_minute", "TPM soft limit"),
    ),
)
def test_budget_policy_rejects_soft_limits_above_hard_limits(
    soft_field, hard_field, message
):
    with pytest.raises(ValidationError, match=message):
        BudgetPolicy(**{soft_field: 2, hard_field: 1})


def test_budget_policy_loads_typed_environment_settings(monkeypatch):
    monkeypatch.setenv("LUCY_GOVERNANCE_SESSION_HARD_USD", "1.25")
    monkeypatch.setenv("LUCY_GOVERNANCE_HARD_REQUESTS_PER_MINUTE", "12")

    policy = BudgetPolicy()

    assert policy.session_hard_usd == 1.25
    assert policy.hard_requests_per_minute == 12

    monkeypatch.setenv("LUCY_GOVERNANCE_HARD_REQUESTS_PER_MINUTE", "invalid")
    with pytest.raises(ValidationError):
        BudgetPolicy()


@pytest.mark.parametrize(
    "field",
    ("session_soft_usd", "session_hard_usd", "agent_soft_usd", "agent_hard_usd"),
)
def test_usd_policy_fields_enforce_finite_nonnegative_bounds(field):
    assert getattr(BudgetPolicy(**{field: 0}), field) == 0
    assert getattr(BudgetPolicy(**{field: MAX_USAGE_UNITS}), field) == MAX_USAGE_UNITS
    for invalid in (-1, True, float("inf"), MAX_USAGE_UNITS + 1):
        with pytest.raises(ValidationError):
            BudgetPolicy(**{field: invalid})


@pytest.mark.parametrize(
    "field",
    (
        "soft_requests_per_minute",
        "hard_requests_per_minute",
        "soft_tokens_per_minute",
        "hard_tokens_per_minute",
    ),
)
def test_rate_policy_fields_enforce_integer_bounds(field):
    assert getattr(BudgetPolicy(**{field: 1}), field) == 1
    assert getattr(BudgetPolicy(**{field: MAX_BUDGET_RATE_LIMIT}), field) == (
        MAX_BUDGET_RATE_LIMIT
    )
    for invalid in (0, -1, True, 1.0, MAX_BUDGET_RATE_LIMIT + 1):
        with pytest.raises(ValidationError):
            BudgetPolicy(**{field: invalid})


def test_session_budget_warns_once_then_returns_hard_action():
    controller = BudgetController(
        BudgetPolicy(session_soft_usd=1, session_hard_usd=2), ManualClock()
    )
    lease = controller.open_session("session-a", "agent-a")

    assert controller.record_cost(lease, 0.5) == ()
    soft = controller.record_cost(lease, 0.6)
    assert [(notice.scope, notice.level) for notice in soft] == [
        (BudgetScope.SESSION, BudgetLevel.SOFT)
    ]
    assert controller.record_cost(lease, 0.1) == ()
    hard = controller.record_cost(lease, 1.0)
    assert [(notice.scope, notice.level) for notice in hard] == [
        (BudgetScope.SESSION, BudgetLevel.HARD)
    ]
    assert hard[0].current == pytest.approx(2.2)
    assert hard[0].limit == 2


def test_agent_budget_accumulates_across_sessions():
    controller = BudgetController(
        BudgetPolicy(agent_soft_usd=1, agent_hard_usd=2), ManualClock()
    )

    leases = {}
    for session_id, agent_id in (
        ("session-a", "agent-a"),
        ("session-b", "agent-a"),
        ("session-c", "agent-a"),
        ("session-d", "agent-b"),
        ("session-e", "agent-a"),
    ):
        leases[session_id] = controller.open_session(session_id, agent_id)

    assert controller.record_cost(leases["session-a"], 0.75) == ()
    soft = controller.record_cost(leases["session-b"], 0.5)
    hard = controller.record_cost(leases["session-c"], 1.0)

    assert soft[0].scope == BudgetScope.AGENT
    assert soft[0].current == pytest.approx(1.25)
    assert hard[0].scope == BudgetScope.AGENT
    assert hard[0].current == pytest.approx(2.25)
    assert controller.record_cost(leases["session-d"], 0.75) == ()
    repeated_hard = controller.record_cost(leases["session-e"], 0.1)
    assert [(notice.scope, notice.level) for notice in repeated_hard] == [
        (BudgetScope.AGENT, BudgetLevel.HARD)
    ]


@pytest.mark.asyncio
async def test_hard_call_budget_emits_session_end_and_stops_later_turns():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    llm = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["First."], usage=UsageReport(1_000, 0)),
            ScriptedLlmTurn(tokens=["Second."], usage=UsageReport(1_000, 0)),
        ],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    scenario = SyntheticCallScenario(
        name="budget-stop",
        objective="stop after the first over-budget turn",
        turns=[
            SyntheticTurn(speaker="caller", text="First request"),
            SyntheticTurn(speaker="caller", text="Second request"),
        ],
        expected_outcome="budget stop",
    )
    gateway = LocalGatewaySimulator(scenario, clock)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    records = await VoiceSession(
        "session-budget",
        gateway,
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=PriceBook(version="budget", llm_prompt_per_1k=1),
        budget_controller=controller,
        agent_id="agent-a",
    ).run()
    tracer.flush()

    assert len(records) == 1
    assert [
        directive
        for directive in gateway.directives
        if isinstance(directive, SessionEnd)
    ] == [SessionEnd(reason="budget_exceeded")]
    budget_spans = [
        event
        for event in exporter.events
        if event.type == "span" and event.name == "budget.check"
    ]
    assert len(budget_spans) == 1
    assert budget_spans[0].status == "error"
    assert budget_spans[0].attributes["budget.scope"] == "session"


@pytest.mark.asyncio
async def test_agent_usd_hard_limit_ends_voice_session():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(agent_hard_usd=1), clock)
    prior = controller.open_session("prior", "agent-a")
    controller.record_cost(prior, 0.75)
    controller.close_session(prior)
    gateway = LocalGatewaySimulator(
        SyntheticCallScenario(
            name="agent-budget",
            objective="stop on an agent budget",
            turns=[SyntheticTurn(speaker="caller", text="Hello")],
            expected_outcome="budget stop",
        ),
        clock,
    )

    async def responder(text: str) -> str:
        del text
        return "Done."

    await VoiceSession(
        "agent-budget",
        gateway,
        responder,
        clock=clock,
        pricebook=PriceBook(version="agent", tts_per_1k_characters=1_000),
        budget_controller=controller,
        agent_id="agent-a",
    ).run()

    assert gateway.directives.count(SessionEnd(reason="budget_exceeded")) == 1


@pytest.mark.asyncio
async def test_rpm_hard_limit_ends_voice_session_before_second_dispatch():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_requests_per_minute=1), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["First."], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        BudgetedLlmProvider(
            inner,
            controller,
            session_id="rpm-call",
            agent_id="agent-a",
        ),
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    gateway = LocalGatewaySimulator(
        SyntheticCallScenario(
            name="rpm-call",
            objective="stop before the second dispatch",
            turns=[
                SyntheticTurn(speaker="caller", text="First"),
                SyntheticTurn(speaker="caller", text="Second"),
            ],
            expected_outcome="budget stop",
        ),
        clock,
    )

    await VoiceSession("rpm-call", gateway, driver=driver, clock=clock).run()

    assert len(inner.seen_requests) == 1
    assert gateway.directives.count(SessionEnd(reason="budget_exceeded")) == 1
    assert controller._session_leases == {}


@pytest.mark.asyncio
async def test_soft_usd_rpm_and_tpm_limits_emit_spans_without_ending_call():
    clock = ManualClock()
    controller = BudgetController(
        BudgetPolicy(
            session_soft_usd=0.5,
            session_hard_usd=2,
            soft_requests_per_minute=1,
            hard_requests_per_minute=3,
            soft_tokens_per_minute=1,
            hard_tokens_per_minute=10,
        ),
        clock,
    )
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Done."], usage=UsageReport(4, 2))],
        clock,
        token_interval_ms=0,
    )
    llm = BudgetedLlmProvider(
        inner,
        controller,
        session_id="soft-call",
        agent_id="agent-a",
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    scenario = SyntheticCallScenario(
        name="soft-call",
        objective="emit soft warnings and finish normally",
        turns=[SyntheticTurn(speaker="caller", text="Hello")],
        expected_outcome="answered",
    )
    gateway = LocalGatewaySimulator(scenario, clock)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    records = await VoiceSession(
        "soft-call",
        gateway,
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=PriceBook(version="budget", llm_prompt_per_1k=250),
    ).run()
    tracer.flush()

    assert len(records) == 1
    assert not any(isinstance(item, SessionEnd) for item in gateway.directives)
    budget_spans = [
        event
        for event in exporter.events
        if event.type == "span" and event.name == "budget.check"
    ]
    assert {event.attributes["budget.scope"] for event in budget_spans} == {
        "session",
        "rpm",
        "tpm",
    }
    assert {event.status for event in budget_spans} == {"fallback"}


def test_budget_controller_requires_nonempty_identities():
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), ManualClock())

    with pytest.raises(ValueError, match="session_id"):
        controller.open_session("", "agent-a")
    with pytest.raises(ValueError, match="agent_id"):
        controller.open_session("session-a", "")
    assert controller._session_leases == {}
    assert controller._agent_spend == {}


def test_budget_identity_length_boundary_is_enforced_transactionally():
    controller = BudgetController(BudgetPolicy(), ManualClock())
    session_id = "s" * MAX_BUDGET_IDENTITY_LENGTH
    agent_id = "a" * MAX_BUDGET_IDENTITY_LENGTH
    lease = controller.open_session(session_id, agent_id)
    controller.close_session(lease)

    with pytest.raises(ValueError, match="session_id"):
        controller.open_session(session_id + "x", agent_id)
    with pytest.raises(ValueError, match="agent_id"):
        controller.open_session("valid", agent_id + "x")

    assert session_id + "x" not in controller._session_leases
    assert "valid" not in controller._session_leases


def test_constructed_but_unrun_session_does_not_reserve_budget_identity():
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), ManualClock())

    async def responder(text: str) -> str:
        return text

    VoiceSession(
        "not-run",
        _BlockingSessionTransport(),
        responder,
        budget_controller=controller,
        agent_id="agent-a",
    )

    assert "not-run" not in controller._session_leases


def test_constructed_but_unused_llm_wrapper_does_not_reserve_budget_identity():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(), clock)

    BudgetedLlmProvider(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        controller,
        session_id="not-run",
        agent_id="agent-a",
    )

    assert controller._session_leases == {}
    assert controller._agent_spend == {}


@pytest.mark.asyncio
async def test_tpm_hard_action_closes_inner_llm_stream():
    clock = ManualClock()
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(4, 2))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock),
        session_id="session-a",
        agent_id="agent-a",
    )

    with pytest.raises(BudgetExceeded):
        await _collect(provider)

    assert inner.finalized_streams == 1


@pytest.mark.asyncio
async def test_budget_error_survives_inner_stream_finalization_failure():
    clock = ManualClock()
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(4, 2))],
        clock,
        token_interval_ms=0,
        finalization_error=RuntimeError("cleanup failed"),
    )
    provider = BudgetedLlmProvider(
        inner,
        BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock),
        session_id="session-a",
        agent_id="agent-a",
    )

    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert isinstance(exc_info.value.__context__, RuntimeError)
    assert str(exc_info.value.__context__) == "cleanup failed"


@pytest.mark.asyncio
async def test_cancelled_unmetered_stream_poisoned_until_lease_is_closed():
    clock = ManualClock()
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["pending"], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=1_000,
    )
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=10), clock)
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="cancelled-stream",
        agent_id="agent-a",
    )
    consumer = asyncio.create_task(_collect(provider))
    for _ in range(10):
        if inner.seen_requests:
            break
        await asyncio.sleep(0)
    consumer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert inner.cancelled is True
    assert inner.finalized_streams == 1
    with pytest.raises(BudgetMeteringError, match="poisoned"):
        await _collect(provider)

    provider.close_budget_session()
    assert controller._session_leases == {}


@pytest.mark.asyncio
async def test_stale_stream_cannot_mutate_reopened_budget_lease():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=10), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["late"], usage=UsageReport(2, 1))],
        clock,
        token_interval_ms=1_000,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="reused",
        agent_id="agent-a",
    )
    stale = asyncio.create_task(_collect(provider))
    for _ in range(10):
        if inner.seen_requests:
            break
        await asyncio.sleep(0)
    provider.close_budget_session()
    provider.open_budget_session()
    clock.advance(1_000)

    with pytest.raises(ValueError, match="invalid or stale"):
        await stale
    assert controller._token_totals.get("agent-a", 0) == 0
    provider.close_budget_session()


@pytest.mark.asyncio
async def test_standalone_llm_caller_releases_session_identity():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(), clock)

    for session_id in ("session-a", "session-b"):
        provider = BudgetedLlmProvider(
            LocalLlmSimulator(
                [ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0))],
                clock,
                token_interval_ms=0,
            ),
            controller,
            session_id=session_id,
            agent_id="agent-a",
        )
        try:
            await _collect(provider)
        finally:
            provider.close_budget_session()

    assert controller._session_leases == {}
    assert controller._agent_spend == {}


def test_close_session_releases_session_cap_but_keeps_agent_spend():
    controller = BudgetController(
        BudgetPolicy(session_hard_usd=1, agent_hard_usd=1), ManualClock()
    )

    first = controller.open_session("session-a", "agent-a")
    assert controller.record_cost(first, 0.75) == ()
    controller.close_session(first)
    second = controller.open_session("session-b", "agent-a")
    notices = controller.record_cost(second, 0.75)

    assert [(notice.scope, notice.level) for notice in notices] == [
        (BudgetScope.AGENT, BudgetLevel.HARD)
    ]


def test_session_identity_capacity_rejects_only_new_sessions():
    controller = BudgetController(BudgetPolicy(), ManualClock())
    leases = [
        controller.open_session("session-%d" % index, "agent-a")
        for index in range(MAX_BUDGET_IDENTITIES)
    ]

    with pytest.raises(ValueError, match="session budget identity capacity exceeded"):
        controller.open_session("overflow", "agent-a")

    assert len(controller._session_leases) == MAX_BUDGET_IDENTITIES
    assert "overflow" not in controller._session_leases
    assert controller.begin_request(leases[0]) == ()


def test_agent_identity_capacity_rejects_only_new_agents():
    controller = BudgetController(BudgetPolicy(agent_hard_usd=1), ManualClock())
    for index in range(MAX_BUDGET_IDENTITIES):
        session_id = "session-%d" % index
        lease = controller.open_session(session_id, "agent-%d" % index)
        controller.close_session(lease)

    with pytest.raises(ValueError, match="agent budget identity capacity exceeded"):
        controller.open_session("overflow", "new-agent")

    assert len(controller._agent_spend) == MAX_BUDGET_IDENTITIES
    assert "new-agent" not in controller._agent_spend
    assert "overflow" not in controller._session_leases
    existing = controller.open_session("existing", "agent-0")
    assert controller.begin_request(existing) == ()


def test_disabled_policy_does_not_retain_historical_agent_state():
    controller = BudgetController(BudgetPolicy(), ManualClock())

    for index in range(MAX_BUDGET_IDENTITIES + 1):
        lease = controller.open_session("session-%d" % index, "agent-%d" % index)
        assert controller.begin_request(lease) == ()
        controller.close_session(lease)

    assert controller._agent_spend == {}
    assert controller._requests == {}
    assert controller._tokens == {}
    assert controller._token_totals == {}


def test_rpm_soft_only_window_state_is_bounded():
    controller = BudgetController(
        BudgetPolicy(soft_requests_per_minute=1), ManualClock()
    )
    lease = controller.open_session("session-a", "agent-a")

    for _ in range(MAX_BUDGET_RATE_LIMIT + 1):
        controller.begin_request(lease)

    assert len(controller._requests["agent-a"]) == MAX_BUDGET_RATE_LIMIT


def test_tpm_soft_only_window_state_is_bounded():
    controller = BudgetController(BudgetPolicy(soft_tokens_per_minute=1), ManualClock())
    lease = controller.open_session("session-a", "agent-a")
    usage = UsageReport(1, 0)

    for _ in range(MAX_BUDGET_RATE_LIMIT + 1):
        controller.record_tokens(lease, usage)

    assert len(controller._tokens["agent-a"]) == MAX_BUDGET_RATE_LIMIT
    assert controller._token_totals["agent-a"] == MAX_BUDGET_RATE_LIMIT


class _BlockingSessionTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="cancelled", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        self.started.set()
        await asyncio.Event().wait()

    async def send(self, envelope, payload) -> None:
        del envelope, payload


class _ActiveTurnCancellationTransport:
    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="active", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="active",
                turn_id="turn-a",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await asyncio.Event().wait()

    async def send(self, envelope, payload) -> None:
        del envelope, payload


class _SessionEndWithActiveTurnTransport:
    def __init__(self, responder_started: asyncio.Event) -> None:
        self._responder_started = responder_started
        self.directives = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="active-end", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="active-end",
                turn_id="turn-a",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self._responder_started.wait()
        yield ControlEvent(
            Envelope(type="session.ended", session_id="active-end", seq=3, ts_ms=20),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.directives.append(payload)


class _BargeWhileThinkingTransport:
    def __init__(self, responder_started: asyncio.Event) -> None:
        self.responder_started = responder_started
        self.directives = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="barge", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final", session_id="barge", turn_id="turn-a", seq=2, ts_ms=10
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self.responder_started.wait()
        yield ControlEvent(
            Envelope(
                type="barge_in", session_id="barge", turn_id="turn-a", seq=3, ts_ms=20
            ),
            BargeIn(at_ms=20, during="thinking"),
        )
        yield ControlEvent(
            Envelope(type="session.ended", session_id="barge", seq=4, ts_ms=21),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.directives.append(payload)


@pytest.mark.asyncio
async def test_cancelled_voice_session_releases_budget_identity():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)
    transport = _BlockingSessionTransport()

    async def responder(text: str) -> str:
        return text

    session = VoiceSession(
        "cancelled",
        transport,
        responder,
        clock=clock,
        budget_controller=controller,
        agent_id="agent-a",
    )
    task = asyncio.create_task(session.run())
    await transport.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    reopened = controller.open_session("cancelled", "agent-a")
    assert controller.record_cost(reopened, 0.75) == ()


@pytest.mark.asyncio
async def test_cancelled_voice_session_cancels_active_turn_task():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)
    responder_started = asyncio.Event()
    responder_cancelled = asyncio.Event()

    async def responder(text: str) -> str:
        del text
        responder_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            responder_cancelled.set()
            raise

    session = VoiceSession(
        "active",
        _ActiveTurnCancellationTransport(),
        responder,
        clock=clock,
        budget_controller=controller,
        agent_id="agent-a",
    )
    task = asyncio.create_task(session.run())
    await responder_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert responder_cancelled.is_set()
    assert "active" not in controller._session_leases


@pytest.mark.asyncio
async def test_session_cancellation_preserves_cleanup_failure_context():
    clock = ManualClock()
    responder_started = asyncio.Event()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(text: str) -> str:
        del text
        responder_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("turn cleanup failed") from exc

    session = VoiceSession(
        "active",
        _ActiveTurnCancellationTransport(),
        responder,
        clock=clock,
        tracer=tracer,
    )
    run_task = asyncio.create_task(session.run())
    await responder_started.wait()
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await run_task
    tracer.flush()

    assert isinstance(exc_info.value.__context__, RuntimeError)
    assert str(exc_info.value.__context__) == "turn cleanup failed"
    cleanup = next(
        event
        for event in exporter.events
        if event.type == "span" and event.name == "session.task_cleanup"
    )
    assert cleanup.attributes["task.error_count"] == "1"


@pytest.mark.asyncio
async def test_session_end_detaches_non_cooperative_active_turn():
    clock = ManualClock()
    responder_started = asyncio.Event()
    cancel_seen = asyncio.Event()
    release = asyncio.Event()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(text: str) -> str:
        del text
        responder_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release.wait()
            return "late output"

    transport = _SessionEndWithActiveTurnTransport(responder_started)
    session = VoiceSession(
        "active-end",
        transport,
        responder,
        clock=clock,
        tracer=tracer,
    )
    run_task = asyncio.create_task(session.run())
    for _ in range(40):
        if run_task.done():
            break
        await asyncio.sleep(0)
    try:
        assert run_task.done(), "active turn blocked normal session shutdown"
        assert len(session._detached_tasks) == 1
    finally:
        release.set()
        await asyncio.gather(run_task, return_exceptions=True)
        for _ in range(10):
            if not session._detached_tasks:
                break
            await asyncio.sleep(0)
    tracer.flush()

    assert cancel_seen.is_set()
    cleanup = next(
        event
        for event in exporter.events
        if event.type == "span" and event.name == "session.task_cleanup"
    )
    assert cleanup.status == "error"
    assert cleanup.attributes["task.detached_count"] == "1"
    assert transport.directives == []
    assert session._detached_tasks == set()


@pytest.mark.asyncio
async def test_barge_in_fences_late_non_cooperative_turn_output():
    clock = ManualClock()
    started = asyncio.Event()
    release = asyncio.Event()
    transport = _BargeWhileThinkingTransport(started)

    async def responder(text: str) -> str:
        del text
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return "late output"

    session = VoiceSession("barge", transport, responder, clock=clock)
    await session.run()
    assert len(session._detached_tasks) == 1
    assert not any(isinstance(item, TtsSpeak) for item in transport.directives)

    release.set()
    for _ in range(10):
        if not session._detached_tasks:
            break
        await asyncio.sleep(0)
    assert session._detached_tasks == set()
    assert not any(isinstance(item, TtsSpeak) for item in transport.directives)


@pytest.mark.asyncio
async def test_cancelled_voice_session_cancels_prefetch_and_preserves_background():
    clock = ManualClock()
    transport = _BlockingSessionTransport()
    prefetch_cancelled = asyncio.Event()
    background_cancelled = asyncio.Event()

    async def responder(text: str) -> str:
        return text

    async def owned_task(cancelled: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    session = VoiceSession("owned", transport, responder, clock=clock)
    session._prefetch_tasks.add(asyncio.create_task(owned_task(prefetch_cancelled)))
    background_task = asyncio.create_task(owned_task(background_cancelled))
    session._background_tasks.add(background_task)
    task = asyncio.create_task(session.run())
    await transport.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert prefetch_cancelled.is_set()
    assert not background_cancelled.is_set()
    assert session._prefetch_tasks == set()
    assert session._background_tasks == {background_task}

    background_task.cancel()
    await asyncio.gather(background_task, return_exceptions=True)
    session._background_tasks.discard(background_task)
    assert session._background_tasks == set()


class _ReversedTimestampTransport:
    def __init__(self) -> None:
        self.barrier = asyncio.Event()
        self.sent: list[object] = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="reversed", seq=1, ts_ms=100),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="reversed",
                turn_id="turn-a",
                seq=2,
                ts_ms=90,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self.barrier.wait()
        yield ControlEvent(
            Envelope(type="session.ended", session_id="reversed", seq=3, ts_ms=80),
            SessionEnded(reason="malformed_clock"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.sent.append(payload)
        if isinstance(payload, TtsStreamEnd):
            self.barrier.set()


@pytest.mark.asyncio
async def test_reversed_control_timestamps_cannot_bypass_valid_llm_budget():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    llm = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(1_000, 0))],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    transport = _ReversedTimestampTransport()

    await VoiceSession(
        "reversed",
        transport,
        driver=driver,
        clock=clock,
        pricebook=PriceBook(version="budget", llm_prompt_per_1k=1),
        budget_controller=controller,
        agent_id="agent-a",
    ).run()

    assert any(
        isinstance(directive, SessionEnd) and directive.reason == "budget_exceeded"
        for directive in transport.sent
    )


@pytest.mark.asyncio
async def test_rpm_hard_limit_rejects_before_dispatch_and_resets_with_manual_clock():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_requests_per_minute=1), clock)
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["one"], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(tokens=["two"], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="session-a",
        agent_id="agent-a",
    )

    await _collect(provider)
    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert exc_info.value.notice.scope == BudgetScope.RPM
    assert exc_info.value.notice.level == BudgetLevel.HARD
    assert len(inner.seen_requests) == 1

    with pytest.raises(BudgetExceeded):
        await _collect(provider)
    assert len(inner.seen_requests) == 1

    clock.advance(59_999)
    with pytest.raises(BudgetExceeded):
        await _collect(provider)
    clock.advance(1)
    events = await _collect(provider)
    assert any(isinstance(event, UsageReport) for event in events)
    assert len(inner.seen_requests) == 2


@pytest.mark.asyncio
async def test_tpm_hard_boundary_rejects_before_dispatch_until_window_expires():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock)
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=[], usage=UsageReport(3, 2)),
            ScriptedLlmTurn(tokens=[], usage=UsageReport(1, 0)),
        ],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="tpm-boundary",
        agent_id="agent-a",
    )

    assert any(isinstance(event, UsageReport) for event in await _collect(provider))
    assert len(inner.seen_requests) == 1

    with pytest.raises(BudgetExceeded):
        await _collect(provider)
    clock.advance(59_999)
    with pytest.raises(BudgetExceeded):
        await _collect(provider)
    assert len(inner.seen_requests) == 1

    clock.advance(1)
    assert any(isinstance(event, UsageReport) for event in await _collect(provider))
    assert len(inner.seen_requests) == 2


def test_rpm_and_tpm_windows_aggregate_by_agent_across_sessions():
    clock = ManualClock()
    rpm = BudgetController(BudgetPolicy(hard_requests_per_minute=2), clock)
    lease = rpm.open_session("session-a", "agent-a")
    assert rpm.begin_request(lease) == ()
    rpm.close_session(lease)
    lease = rpm.open_session("session-b", "agent-a")
    assert rpm.begin_request(lease) == ()
    rpm.close_session(lease)
    session_c = rpm.open_session("session-c", "agent-a")
    rpm_hard = rpm.begin_request(session_c)
    assert rpm_hard[0].scope == BudgetScope.RPM
    session_d = rpm.open_session("session-d", "agent-b")
    assert rpm.begin_request(session_d) == ()
    rpm.close_session(session_c)
    rpm.close_session(session_d)

    clock.advance(60_000)
    session_e = rpm.open_session("session-e", "agent-a")
    assert rpm.begin_request(session_e) == ()

    tpm = BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock)
    lease = tpm.open_session("session-a", "agent-a")
    assert tpm.record_tokens(lease, UsageReport(2, 1)) == ()
    tpm.close_session(lease)
    session_b = tpm.open_session("session-b", "agent-a")
    tpm_hard = tpm.record_tokens(session_b, UsageReport(2, 1))
    assert tpm_hard[0].scope == BudgetScope.TPM
    session_c = tpm.open_session("session-c", "agent-b")
    assert tpm.record_tokens(session_c, UsageReport(2, 1)) == ()
    tpm.close_session(session_b)
    tpm.close_session(session_c)

    clock.advance(60_000)
    session_d = tpm.open_session("session-d", "agent-a")
    assert tpm.record_tokens(session_d, UsageReport(2, 1)) == ()


@pytest.mark.asyncio
async def test_non_call_rate_decisions_emit_automatic_observe_spans():
    clock = ManualClock()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    controller = BudgetController(
        BudgetPolicy(
            soft_requests_per_minute=1,
            hard_requests_per_minute=1,
        ),
        clock,
        tracer=tracer,
    )
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="non-call",
        agent_id="agent-a",
    )

    await _collect(provider)
    with pytest.raises(BudgetExceeded):
        await _collect(provider)
    tracer.flush()

    spans = [
        event
        for event in exporter.events
        if event.type == "span" and event.name == "budget.check"
    ]
    assert [(event.status, event.attributes["budget.level"]) for event in spans] == [
        ("fallback", "soft"),
        ("error", "hard"),
    ]


@pytest.mark.asyncio
async def test_tpm_hard_limit_raises_typed_error_from_actual_usage():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock)
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=[], usage=UsageReport(4, 2)),
            ScriptedLlmTurn(tokens=[], usage=UsageReport(1, 1)),
        ],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="session-a",
        agent_id="agent-a",
    )

    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert exc_info.value.notice.scope == BudgetScope.TPM
    assert exc_info.value.notice.current == 6
    assert exc_info.value.usage == UsageReport(4, 2)


@pytest.mark.asyncio
async def test_tpm_hard_action_still_commits_priced_usd_usage():
    clock = ManualClock()
    controller = BudgetController(
        BudgetPolicy(hard_tokens_per_minute=5, agent_hard_usd=0.5), clock
    )
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(1_000, 1))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="combined",
        agent_id="agent-a",
        pricebook=PriceBook(version="combined", llm_prompt_per_1k=1),
    )

    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert exc_info.value.notice.scope == BudgetScope.TPM
    assert controller._agent_spend["agent-a"] == pytest.approx(1.0)


def test_tpm_hard_limit_expires_at_manual_clock_window_boundary():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock)
    lease = controller.open_session("session-a", "agent-a")

    hard = controller.record_tokens(lease, UsageReport(4, 2))
    assert hard[0].level == BudgetLevel.HARD
    clock.advance(59_999)
    still_hard = controller.record_tokens(lease, UsageReport(1, 1))
    assert still_hard[0].level == BudgetLevel.HARD
    clock.advance(1)

    assert controller.record_tokens(lease, UsageReport(1, 1)) == ()


def test_tpm_soft_notice_resets_after_manual_clock_window():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(soft_tokens_per_minute=5), clock)
    lease = controller.open_session("session-a", "agent-a")

    first = controller.record_tokens(lease, UsageReport(3, 2))
    assert first[0].level == BudgetLevel.SOFT
    assert controller.record_tokens(lease, UsageReport(1, 0)) == ()
    clock.advance(60_000)

    second = controller.record_tokens(lease, UsageReport(3, 2))
    assert second[0].level == BudgetLevel.SOFT


@pytest.mark.asyncio
async def test_tpm_hard_limit_ends_a_voice_session_and_preserves_usage_cost():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=5), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Goodbye."], usage=UsageReport(4, 2))],
        clock,
        token_interval_ms=0,
        finalization_error=RuntimeError("call cleanup failed"),
    )
    llm = BudgetedLlmProvider(
        inner,
        controller,
        session_id="rate-call",
        agent_id="agent-a",
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    scenario = SyntheticCallScenario(
        name="rate-call",
        objective="end the call on a hard TPM action",
        turns=[
            SyntheticTurn(speaker="caller", text="Hello"),
            SyntheticTurn(speaker="caller", text="Are you there?"),
        ],
        expected_outcome="budget stop",
    )
    gateway = LocalGatewaySimulator(scenario, clock)
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    records = await VoiceSession(
        "rate-call",
        gateway,
        driver=driver,
        clock=clock,
        tracer=tracer,
        pricebook=PriceBook(version="budget", llm_prompt_per_1k=1),
    ).run()
    tracer.flush()

    assert records[0].cost is not None
    assert records[0].cost.llm_cost == pytest.approx(0.004)
    assert records[0].assistant_text == "Goodbye."
    assert records[0].interrupted is False
    assert len(records) == 1
    assert len(inner.seen_requests) == 1
    assert any(
        isinstance(directive, SessionEnd) and directive.reason == "budget_exceeded"
        for directive in gateway.directives
    )
    hard_span = next(
        event
        for event in exporter.events
        if event.type == "span"
        and event.name == "budget.check"
        and event.status == "error"
    )
    assert hard_span.attributes["budget.scope"] == "tpm"


def test_voice_session_rejects_priced_budgeted_driver():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1.5), clock)
    pricebook = PriceBook(version="shared", llm_prompt_per_1k=1)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Done."], usage=UsageReport(1_000, 0))],
        clock,
        token_interval_ms=0,
    )
    llm = BudgetedLlmProvider(
        inner,
        controller,
        session_id="single-charge",
        agent_id="agent-a",
        pricebook=pricebook,
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    with pytest.raises(ValueError, match="voice sessions own call pricing"):
        VoiceSession(
            "single-charge",
            _ImmediateEndTransport(),
            driver=driver,
            clock=clock,
            pricebook=pricebook,
        )


def test_voice_session_rejects_mismatched_budgeted_llm_session():
    clock = ManualClock()
    llm = BudgetedLlmProvider(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        BudgetController(BudgetPolicy(hard_tokens_per_minute=2), clock),
        session_id="wrapper-session",
        agent_id="voice-agent",
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
    )

    with pytest.raises(ValueError, match="budget binding"):
        VoiceSession(
            "voice-session",
            _ImmediateEndTransport(),
            driver=driver,
            clock=clock,
        )


@pytest.mark.parametrize(
    "explicit",
    (
        {"budget_controller": "controller", "agent_id": "agent-a"},
        {"budget_controller": "controller"},
        {"agent_id": "agent-a"},
    ),
)
def test_voice_session_rejects_explicit_budget_arguments_with_driver_binding(explicit):
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=2), clock)
    driver = CascadedTurnDriver(
        BudgetedLlmProvider(
            LocalLlmSimulator([], clock, token_interval_ms=0),
            controller,
            session_id="bound-session",
            agent_id="agent-a",
        ),
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
    )
    arguments = {
        key: controller if value == "controller" else value
        for key, value in explicit.items()
    }

    with pytest.raises(ValueError, match="budget binding"):
        VoiceSession(
            "bound-session",
            _ImmediateEndTransport(),
            driver=driver,
            clock=clock,
            **arguments,
        )


@pytest.mark.parametrize(
    "arguments",
    (
        {"budget_controller": BudgetController(BudgetPolicy(), ManualClock())},
        {"agent_id": "agent-a"},
    ),
)
def test_voice_session_rejects_partial_explicit_budget_configuration(arguments):
    async def responder(text: str) -> str:
        return text

    with pytest.raises(ValueError, match="configured together"):
        VoiceSession(
            "partial-budget",
            _ImmediateEndTransport(),
            responder,
            clock=ManualClock(),
            **arguments,
        )


def test_voice_session_rejects_unwrapped_driver_rate_governance():
    clock = ManualClock()
    driver = CascadedTurnDriver(
        LocalLlmSimulator([], clock, token_interval_ms=0),
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
    )

    with pytest.raises(ValueError, match="BudgetedLlmProvider"):
        VoiceSession(
            "unwrapped-rate",
            _ImmediateEndTransport(),
            driver=driver,
            clock=clock,
            budget_controller=BudgetController(
                BudgetPolicy(hard_requests_per_minute=1), clock
            ),
            agent_id="agent-a",
        )


@pytest.mark.asyncio
async def test_unwrapped_usd_governed_driver_requires_usage_report():
    clock = ManualClock()
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["Unmetered."],
                usage=UsageReport(1_000, 0),
                omit_usage=True,
            )
        ],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        inner,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    gateway = LocalGatewaySimulator(
        SyntheticCallScenario(
            name="unwrapped-unmetered",
            objective="stop an unmetered USD-governed driver",
            turns=[SyntheticTurn(speaker="caller", text="Hello")],
            expected_outcome="budget stop",
        ),
        clock,
    )

    await VoiceSession(
        "unwrapped-unmetered",
        gateway,
        driver=driver,
        clock=clock,
        pricebook=PriceBook(version="unwrapped", llm_prompt_per_1k=1),
        budget_controller=BudgetController(BudgetPolicy(session_hard_usd=0.5), clock),
        agent_id="agent-a",
    ).run()

    assert gateway.directives.count(SessionEnd(reason="budget_exceeded")) == 1
    assert inner.finalized_streams == 1


@pytest.mark.asyncio
async def test_non_llm_tts_cost_can_end_voice_session_budget():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)
    scenario = SyntheticCallScenario(
        name="tts-budget",
        objective="enforce a TTS-only budget",
        turns=[SyntheticTurn(speaker="caller", text="Hello")],
        expected_outcome="budget stop",
    )
    gateway = LocalGatewaySimulator(scenario, clock)

    async def responder(text: str) -> str:
        del text
        return "Hello."

    await VoiceSession(
        "tts-budget",
        gateway,
        responder,
        clock=clock,
        pricebook=PriceBook(version="tts", tts_per_1k_characters=1_000),
        budget_controller=controller,
        agent_id="agent-a",
    ).run()

    assert any(
        isinstance(item, SessionEnd) and item.reason == "budget_exceeded"
        for item in gateway.directives
    )


@pytest.mark.asyncio
async def test_rate_soft_limit_emits_one_warning_per_window():
    clock = ManualClock()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])
    controller = BudgetController(
        BudgetPolicy(
            soft_requests_per_minute=1,
            hard_requests_per_minute=3,
        ),
        clock,
        tracer=tracer,
    )
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0)),
            ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0)),
            ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0)),
        ],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="session-a",
        agent_id="agent-a",
    )

    await _collect(provider)
    await _collect(provider)
    tracer.flush()
    warnings = [
        event
        for event in exporter.events
        if event.type == "span"
        and event.name == "budget.check"
        and event.status == "fallback"
    ]
    assert len(warnings) == 1

    clock.advance(60_000)
    await _collect(provider)
    tracer.flush()
    warnings = [
        event
        for event in exporter.events
        if event.type == "span"
        and event.name == "budget.check"
        and event.status == "fallback"
    ]
    assert len(warnings) == 2


@pytest.mark.asyncio
async def test_standalone_llm_usd_budget_raises_from_priced_usage():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(1_000, 0))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="priced-llm",
        agent_id="agent-a",
        pricebook=PriceBook(version="standalone", llm_prompt_per_1k=1),
    )

    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert exc_info.value.notice.scope == BudgetScope.SESSION
    assert exc_info.value.notice.current == pytest.approx(1.0)
    assert exc_info.value.usage == UsageReport(1_000, 0)


@pytest.mark.asyncio
async def test_standalone_usd_budget_requires_pricebook_before_provider_dispatch():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(1_000, 0))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="unpriced-llm",
        agent_id="agent-a",
    )

    with pytest.raises(BudgetMeteringError, match="price book"):
        await _collect(provider)

    assert inner.seen_requests == []


@pytest.mark.asyncio
async def test_breached_usd_ledger_blocks_llm_before_provider_dispatch():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0))],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        controller,
        session_id="priced-llm",
        agent_id="agent-a",
        pricebook=PriceBook(version="standalone", llm_prompt_per_1k=1),
    )
    lease = provider.open_budget_session()
    controller.record_cost(lease, 1.0)

    with pytest.raises(BudgetExceeded) as exc_info:
        await _collect(provider)

    assert exc_info.value.notice.scope == BudgetScope.SESSION
    assert inner.seen_requests == []


def test_standalone_usd_budget_rejects_non_usd_pricebook():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)

    with pytest.raises(ValueError, match="USD budget requires a USD price book"):
        BudgetedLlmProvider(
            LocalLlmSimulator([], clock, token_interval_ms=0),
            controller,
            session_id="wrong-currency",
            agent_id="agent-a",
            pricebook=PriceBook(version="eur", currency="EUR"),
        )


def test_large_finite_cost_saturates_and_returns_hard_action():
    controller = BudgetController(
        BudgetPolicy(session_hard_usd=MAX_USAGE_UNITS), ManualClock()
    )
    lease = controller.open_session("large-cost", "agent-a")

    notices = controller.record_cost(lease, float(MAX_USAGE_UNITS) * 1_000)

    assert [(notice.scope, notice.level) for notice in notices] == [
        (BudgetScope.SESSION, BudgetLevel.HARD)
    ]
    assert notices[0].current == MAX_USAGE_UNITS + 1


@pytest.mark.parametrize("invalid_cost", [True, -1.0, float("nan"), float("inf")])
def test_invalid_cost_does_not_mutate_live_ledger(invalid_cost):
    controller = BudgetController(BudgetPolicy(), ManualClock())
    lease = controller.open_session("invalid", "agent-a")

    with pytest.raises(ValueError, match="finite nonnegative"):
        controller.record_cost(lease, invalid_cost)

    assert controller._agent_spend == {}
    assert controller._session_spend == {}


def test_duplicate_and_stale_budget_leases_cannot_reset_live_session():
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), ManualClock())
    first_lease = controller.open_session("shared", "agent-a")
    controller.record_cost(first_lease, 0.75)

    with pytest.raises(ValueError, match="already active") as exc_info:
        controller.open_session("shared", "agent-b")
    assert "agent-a" not in str(exc_info.value)

    controller.close_session(first_lease)
    second_lease = controller.open_session("shared", "agent-a")
    controller.record_cost(second_lease, 0.75)
    with pytest.raises(ValueError, match="invalid or stale"):
        controller.record_cost(first_lease, 0.5)
    with pytest.raises(ValueError, match="invalid or stale"):
        controller.close_session(first_lease)

    notices = controller.record_cost(second_lease, 0.5)
    assert [(notice.scope, notice.level) for notice in notices] == [
        (BudgetScope.SESSION, BudgetLevel.HARD)
    ]
    controller.close_session(second_lease)


@pytest.mark.asyncio
async def test_usage_governance_rejects_terminal_llm_stream_without_metering():
    clock = ManualClock()
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["unmetered"],
                usage=UsageReport(1, 1),
                omit_usage=True,
            )
        ],
        clock,
        token_interval_ms=0,
    )
    provider = BudgetedLlmProvider(
        inner,
        BudgetController(BudgetPolicy(hard_tokens_per_minute=10), clock),
        session_id="unmetered",
        agent_id="agent-a",
    )

    with pytest.raises(BudgetMeteringError, match="usage report"):
        await _collect(provider)

    assert inner.finalized_streams == 1


@pytest.mark.asyncio
async def test_missing_llm_metering_ends_governed_voice_session():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_tokens_per_minute=10), clock)
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["Unmetered."],
                usage=UsageReport(1, 1),
                omit_usage=True,
            )
        ],
        clock,
        token_interval_ms=0,
    )
    llm = BudgetedLlmProvider(
        inner,
        controller,
        session_id="unmetered-call",
        agent_id="agent-a",
    )
    driver = CascadedTurnDriver(
        llm,
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    gateway = LocalGatewaySimulator(
        SyntheticCallScenario(
            name="unmetered-call",
            objective="stop an unmetered governed stream",
            turns=[SyntheticTurn(speaker="caller", text="Hello")],
            expected_outcome="budget stop",
        ),
        clock,
    )

    await VoiceSession(
        "unmetered-call",
        gateway,
        driver=driver,
        clock=clock,
    ).run()

    assert any(
        isinstance(item, SessionEnd) and item.reason == "budget_exceeded"
        for item in gateway.directives
    )
    assert inner.finalized_streams == 1
    assert controller._session_leases == {}


@pytest.mark.asyncio
async def test_missing_llm_metering_ends_usd_only_voice_session():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=0.5), clock)
    inner = LocalLlmSimulator(
        [
            ScriptedLlmTurn(
                tokens=["Unmetered."],
                usage=UsageReport(1_000, 0),
                omit_usage=True,
            )
        ],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        BudgetedLlmProvider(
            inner,
            controller,
            session_id="unmetered-usd-call",
            agent_id="agent-a",
        ),
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
        min_flush_chars=1,
    )
    gateway = LocalGatewaySimulator(
        SyntheticCallScenario(
            name="unmetered-usd-call",
            objective="stop an unmetered USD-governed stream",
            turns=[SyntheticTurn(speaker="caller", text="Hello")],
            expected_outcome="budget stop",
        ),
        clock,
    )

    await VoiceSession(
        "unmetered-usd-call",
        gateway,
        driver=driver,
        clock=clock,
        pricebook=PriceBook(version="usd-metering", llm_prompt_per_1k=1),
    ).run()

    assert gateway.directives.count(SessionEnd(reason="budget_exceeded")) == 1
    assert inner.finalized_streams == 1
    assert controller._session_leases == {}


class _ImmediateEndTransport:
    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="normal-end", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(type="session.ended", session_id="normal-end", seq=2, ts_ms=1),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope, payload


class _CountingImmediateEndTransport(_ImmediateEndTransport):
    def __init__(self) -> None:
        self.event_streams = 0
        self.directives = []

    async def events(self):
        self.event_streams += 1
        async for event in super().events():
            yield event

    async def send(self, envelope, payload) -> None:
        del envelope
        self.directives.append(payload)


class _CooperativeBudgetEndTransport:
    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.allow_commit = asyncio.Event()
        self.committed = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="budget-send", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="budget-send",
                turn_id="turn-a",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self.send_started.wait()
        yield ControlEvent(
            Envelope(type="session.ended", session_id="budget-send", seq=3, ts_ms=20),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.send_started.set()
        await self.allow_commit.wait()
        self.committed.append(payload)


class _UsageThenBlockDriver:
    pricebook = None
    budget_binding = None

    def __init__(self) -> None:
        self.report_consumed = asyncio.Event()

    async def run_turn(self, user_text, history, *, turn_context=None):
        del user_text, history, turn_context
        yield TurnDriverReport(
            assistant_text="",
            llm_ms=0,
            usage=UsageReport(1_000, 0),
        )
        self.report_consumed.set()
        await asyncio.Event().wait()


class _BlockingDirectiveTransport:
    def __init__(self) -> None:
        self.send_started = asyncio.Event()
        self.allow_commit = asyncio.Event()
        self.committed = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="blocked-send", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="blocked-send",
                turn_id="turn-a",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self.send_started.wait()
        yield ControlEvent(
            Envelope(type="session.ended", session_id="blocked-send", seq=3, ts_ms=20),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.send_started.set()
        await self.allow_commit.wait()
        self.committed.append(payload)


class _AbortableDirectiveTransport:
    def __init__(self, clock: ManualClock, timeout_ms: int) -> None:
        self.clock = clock
        self.timeout_ms = timeout_ms
        self.send_started = asyncio.Event()
        self.release = asyncio.Event()
        self.aborted = asyncio.Event()
        self.committed = []

    async def events(self):
        yield ControlEvent(
            Envelope(
                type="session.started", session_id="commit-timeout", seq=1, ts_ms=0
            ),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        yield ControlEvent(
            Envelope(
                type="stt.final",
                session_id="commit-timeout",
                turn_id="turn-a",
                seq=2,
                ts_ms=10,
            ),
            SttFinal(text="Hello", provider="local", stt_ms=10),
        )
        await self.send_started.wait()
        self.clock.advance(self.timeout_ms + 1)
        await self.aborted.wait()
        yield ControlEvent(
            Envelope(
                type="session.ended", session_id="commit-timeout", seq=3, ts_ms=20
            ),
            SessionEnded(reason="transport_timeout"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.send_started.set()
        await self.release.wait()
        if not self.aborted.is_set():
            self.committed.append(payload)

    async def abort(self) -> None:
        self.aborted.set()
        self.release.set()


@pytest.mark.asyncio
async def test_voice_session_is_single_use_without_reopening_budget_lease():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)
    transport = _CountingImmediateEndTransport()

    async def responder(text: str) -> str:
        return text

    session = VoiceSession(
        "normal-end",
        transport,
        responder,
        clock=clock,
        budget_controller=controller,
        agent_id="agent-a",
    )

    assert await session.run() == []
    with pytest.raises(RuntimeError, match="single-use"):
        await session.run()

    assert transport.event_streams == 1
    assert controller._session_leases == {}


@pytest.mark.asyncio
async def test_voice_session_preflight_blocks_breached_agent_before_events():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(agent_hard_usd=1), clock)
    prior = controller.open_session("prior", "agent-a")
    controller.record_cost(prior, 1.1)
    controller.close_session(prior)
    transport = _CountingImmediateEndTransport()

    async def responder(text: str) -> str:
        return text

    records = await VoiceSession(
        "preflight-agent",
        transport,
        responder,
        clock=clock,
        budget_controller=controller,
        agent_id="agent-a",
    ).run()

    assert records == []
    assert transport.event_streams == 0
    assert transport.directives == [SessionEnd(reason="budget_exceeded")]


@pytest.mark.asyncio
async def test_budget_end_commit_is_not_cancelled_with_its_turn_task():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(hard_requests_per_minute=2), clock)
    seed = controller.open_session("seed", "agent-a")
    assert controller.begin_request(seed) == ()
    assert controller.begin_request(seed) == ()
    controller.close_session(seed)
    inner = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=[], usage=UsageReport(0, 0))],
        clock,
        token_interval_ms=0,
    )
    driver = CascadedTurnDriver(
        BudgetedLlmProvider(
            inner,
            controller,
            session_id="budget-send",
            agent_id="agent-a",
        ),
        default_model_registry(),
        TEST_PROVIDER,
        TEST_MODEL,
        clock,
        LatencyBudgets(),
    )
    transport = _CooperativeBudgetEndTransport()
    run_task = asyncio.create_task(
        VoiceSession("budget-send", transport, driver=driver, clock=clock).run()
    )
    await transport.send_started.wait()
    try:
        for _ in range(10):
            await asyncio.sleep(0)
        assert not run_task.done()
    finally:
        transport.allow_commit.set()
        await asyncio.gather(run_task, return_exceptions=True)

    assert transport.committed.count(SessionEnd(reason="budget_exceeded")) == 1
    assert inner.seen_requests == []


@pytest.mark.asyncio
async def test_cancelled_session_commits_elapsed_and_available_turn_cost():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(agent_hard_usd=2), clock)
    driver = _UsageThenBlockDriver()
    session = VoiceSession(
        "active",
        _ActiveTurnCancellationTransport(),
        driver=driver,
        clock=clock,
        pricebook=PriceBook(
            version="cancel-final",
            llm_prompt_per_1k=1,
            infra_per_minute=1,
        ),
        budget_controller=controller,
        agent_id="agent-a",
    )
    run_task = asyncio.create_task(session.run())
    await driver.report_consumed.wait()
    clock.advance(30_000)
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task

    after = controller.open_session("after-cancel", "agent-a")
    notices = controller.record_cost(after, 0.6)
    assert [(notice.scope, notice.level) for notice in notices] == [
        (BudgetScope.AGENT, BudgetLevel.HARD)
    ]
    assert notices[0].current == pytest.approx(2.1)


@pytest.mark.asyncio
async def test_session_shutdown_waits_for_started_directive_commit():
    transport = _BlockingDirectiveTransport()

    async def responder(text: str) -> str:
        return "Response to: %s" % text

    session = VoiceSession("blocked-send", transport, responder, clock=ManualClock())
    run_task = asyncio.create_task(session.run())
    await transport.send_started.wait()
    for _ in range(10):
        await asyncio.sleep(0)

    assert not run_task.done()
    transport.allow_commit.set()
    await run_task

    assert len(transport.committed) == 1
    assert isinstance(transport.committed[0], TtsSpeak)
    assert not any(isinstance(item, TtsStreamEnd) for item in transport.committed)


@pytest.mark.asyncio
async def test_directive_commit_timeout_aborts_transport_without_late_output():
    clock = ManualClock()
    budgets = LatencyBudgets(control_commit_ms=25)
    transport = _AbortableDirectiveTransport(clock, budgets.control_commit_ms)

    async def responder(text: str) -> str:
        return "Response to: %s" % text

    run_task = asyncio.create_task(
        VoiceSession(
            "commit-timeout",
            transport,
            responder,
            clock=clock,
            budgets=budgets,
        ).run()
    )
    await transport.send_started.wait()
    for _ in range(30):
        if run_task.done():
            break
        await asyncio.sleep(0)
    try:
        assert run_task.done()
        await run_task
        assert transport.aborted.is_set()
        assert transport.committed == []
    finally:
        transport.release.set()
        if not run_task.done():
            run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_background_task_admission_is_bounded_and_observable():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(text: str) -> str:
        return text

    session = VoiceSession(
        "bounded-background",
        _ImmediateEndTransport(),
        responder,
        clock=ManualClock(),
        tracer=tracer,
    )
    turn = _ActiveTurn("turn-a", "Hello", 0)
    tasks = [
        asyncio.create_task(asyncio.Event().wait())
        for _ in range(MAX_SESSION_BACKGROUND_TASKS)
    ]
    for task in tasks:
        session._adopt_background_task(turn, task)
    release_excess = asyncio.Event()
    excess_started = asyncio.Event()

    async def cancellation_resistant() -> None:
        excess_started.set()
        try:
            await release_excess.wait()
        except asyncio.CancelledError:
            await release_excess.wait()

    excess = asyncio.create_task(cancellation_resistant())
    await excess_started.wait()

    session._adopt_background_task(turn, excess)
    await asyncio.sleep(0)
    tracer.flush()

    assert len(session._background_tasks) == MAX_SESSION_BACKGROUND_TASKS
    assert excess in session._detached_tasks
    assert session._background_capacity_exceeded is True
    cleanup = next(
        event
        for event in exporter.events
        if event.type == "span" and event.name == "session.task_cleanup"
    )
    assert cleanup.status == "error"
    assert cleanup.attributes["task.error_count"] == "1"

    for task in tasks:
        task.cancel()
    release_excess.set()
    await asyncio.gather(*tasks, excess, return_exceptions=True)
    await asyncio.sleep(0)
    assert session._background_tasks == set()
    assert session._detached_tasks == set()


def test_budget_elapsed_cost_saturates_at_control_duration_limit():
    clock = ManualClock()

    async def responder(text: str) -> str:
        return text

    session = VoiceSession(
        "duration-bound",
        _ImmediateEndTransport(),
        responder,
        clock=clock,
        pricebook=PriceBook(version="duration", infra_per_minute=1),
    )
    session._session_clock_start = 0
    clock.advance(MAX_CONTROL_DURATION_MS)
    at_limit = session._priced_budget_usage()
    clock.advance(1)
    beyond_limit = session._priced_budget_usage()

    assert at_limit is not None
    assert beyond_limit is not None
    assert beyond_limit.cost.total_cost == at_limit.cost.total_cost
    assert beyond_limit.cost.total_cost > 0


class _IdleCostTransport:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.directives = []

    async def events(self):
        yield ControlEvent(
            Envelope(type="session.started", session_id="idle-cost", seq=1, ts_ms=0),
            SessionStarted(transport="test", caller="opaque", codecs=["pcmu"]),
        )
        self.clock.advance(60_000)
        yield ControlEvent(
            Envelope(type="session.ended", session_id="idle-cost", seq=2, ts_ms=60_000),
            SessionEnded(reason="completed"),
        )

    async def send(self, envelope, payload) -> None:
        del envelope
        self.directives.append(payload)


@pytest.mark.asyncio
async def test_idle_call_is_ended_when_live_infrastructure_cost_breaches_budget():
    clock = ManualClock()
    transport = _IdleCostTransport(clock)

    async def responder(text: str) -> str:
        return text

    await VoiceSession(
        "idle-cost",
        transport,
        responder,
        clock=clock,
        pricebook=PriceBook(version="idle", infra_per_minute=1),
        budget_controller=BudgetController(BudgetPolicy(session_hard_usd=0.5), clock),
        agent_id="agent-a",
    ).run()

    assert transport.directives == [SessionEnd(reason="budget_exceeded")]


@pytest.mark.asyncio
async def test_normal_session_end_cancels_prefetch_and_preserves_background():
    clock = ManualClock()
    prefetch_cancelled = asyncio.Event()
    background_cancelled = asyncio.Event()

    async def responder(text: str) -> str:
        return text

    async def owned_task(cancelled: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    controller = BudgetController(
        BudgetPolicy(session_hard_usd=1, agent_hard_usd=1), clock
    )
    prior = controller.open_session("prior", "agent-a")
    assert controller.record_cost(prior, 0.75) == ()
    controller.close_session(prior)
    session = VoiceSession(
        "normal-end",
        _ImmediateEndTransport(),
        responder,
        clock=clock,
        budget_controller=controller,
        agent_id="agent-a",
    )
    session._prefetch_tasks.add(asyncio.create_task(owned_task(prefetch_cancelled)))
    background_task = asyncio.create_task(owned_task(background_cancelled))
    session._background_tasks.add(background_task)
    run_task = asyncio.create_task(session.run())
    for _ in range(10):
        await asyncio.sleep(0)
    try:
        assert run_task.done(), "normal session shutdown retained pending owned tasks"
    finally:
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)

    await run_task
    assert prefetch_cancelled.is_set()
    assert not background_cancelled.is_set()
    assert session._prefetch_tasks == set()
    assert session._background_tasks == {background_task}
    after = controller.open_session("after", "agent-a")
    notices = controller.record_cost(after, 0.75)
    assert [(notice.scope, notice.level) for notice in notices] == [
        (BudgetScope.AGENT, BudgetLevel.HARD)
    ]

    background_task.cancel()
    await asyncio.gather(background_task, return_exceptions=True)
    session._background_tasks.discard(background_task)
    assert session._background_tasks == set()


@pytest.mark.asyncio
async def test_non_cooperative_prefetch_is_detached_and_reported():
    clock = ManualClock()
    transport = _BlockingSessionTransport()
    cancel_seen = asyncio.Event()
    release = asyncio.Event()
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter])

    async def responder(text: str) -> str:
        return text

    async def non_cooperative_task() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancel_seen.set()

    session = VoiceSession("detached", transport, responder, clock=clock, tracer=tracer)
    stubborn_task = asyncio.create_task(non_cooperative_task())
    session._prefetch_tasks.add(stubborn_task)
    run_task = asyncio.create_task(session.run())
    await transport.started.wait()
    run_task.cancel()
    for _ in range(30):
        if run_task.done():
            break
        await asyncio.sleep(0)
    try:
        assert run_task.done(), "non-cooperative cleanup blocked session shutdown"
    finally:
        release.set()
        await asyncio.gather(stubborn_task, return_exceptions=True)
        await asyncio.gather(run_task, return_exceptions=True)
    tracer.flush()

    assert cancel_seen.is_set()
    cleanup = next(
        event
        for event in exporter.events
        if event.type == "span" and event.name == "session.task_cleanup"
    )
    assert cleanup.status == "error"
    assert cleanup.attributes["task.detached_count"] == "1"


def test_usd_budget_rejects_non_usd_voice_pricebook():
    clock = ManualClock()
    controller = BudgetController(BudgetPolicy(session_hard_usd=1), clock)

    async def responder(text: str) -> str:
        return text

    with pytest.raises(ValueError, match="USD budget requires a USD price book"):
        VoiceSession(
            "wrong-currency",
            _ImmediateEndTransport(),
            responder,
            clock=clock,
            pricebook=PriceBook(version="eur", currency="EUR"),
            budget_controller=controller,
            agent_id="agent-a",
        )
