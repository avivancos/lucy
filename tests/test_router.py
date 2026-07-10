from typing import AsyncIterator

import pytest
from pydantic import ValidationError

from lucy.clock import ManualClock
from lucy.llm import (
    LlmMessage,
    LlmRequest,
    LlmStreamEvent,
    StreamEnd,
    TokenDelta,
    UsageReport,
)
from lucy.router import (
    AllRoutesFailed,
    Route,
    Router,
    RouterSettings,
    RoutingLlmProvider,
    RoutingPolicy,
)


class TextProvider:
    def __init__(self, text: str, clock: ManualClock, latency_ms: int = 0) -> None:
        self.text = text
        self.clock = clock
        self.latency_ms = latency_ms
        self.calls = 0

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        self.clock.advance(self.latency_ms)
        yield TokenDelta(self.text)
        yield UsageReport(1, 1)
        yield StreamEnd("stop")


class FailingProvider:
    def __init__(self, *, after_token: bool = False) -> None:
        self.after_token = after_token
        self.calls = 0

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.calls += 1
        if self.after_token:
            yield TokenDelta("partial")
        raise RuntimeError("provider unavailable")
        if False:
            yield StreamEnd("error")


def _request() -> LlmRequest:
    return LlmRequest(
        provider="router",
        model="auto",
        messages=[LlmMessage(role="user", content="hello")],
    )


def _route(key, provider, *, priority=100, weight=1.0, low_latency=False):
    return Route(
        key=key,
        provider=provider,
        priority=priority,
        weight=weight,
        low_latency=low_latency,
    )


def test_priority_selection_prefers_lowest_priority_then_key():
    clock = ManualClock()
    router = Router(
        [
            _route("b", TextProvider("b", clock), priority=2),
            _route("a", TextProvider("a", clock), priority=1),
        ],
        RoutingPolicy.PRIORITY,
        clock,
    )

    assert router.select().key == "a"


def test_weighted_selection_uses_injected_entropy():
    clock = ManualClock()
    router = Router(
        [
            _route("small", TextProvider("a", clock), weight=1),
            _route("large", TextProvider("b", clock), weight=3),
        ],
        RoutingPolicy.WEIGHTED,
        clock,
        random_value=lambda: 0.9,
    )

    assert router.select().key == "large"


def test_latency_selection_prefers_low_latency_then_best_ewma():
    clock = ManualClock()
    router = Router(
        [
            _route("slow", TextProvider("slow", clock), low_latency=False),
            _route("warm", TextProvider("warm", clock), low_latency=True),
            _route("fast", TextProvider("fast", clock), low_latency=True),
        ],
        RoutingPolicy.LATENCY,
        clock,
    )
    router.record_first_token("warm", 40)
    router.record_first_token("fast", 10)

    assert router.select().key == "fast"


def test_failed_route_enters_typed_cooldown_then_recovers():
    clock = ManualClock()
    settings = RouterSettings(cooldown_ms=100)
    router = Router(
        [
            _route("first", FailingProvider(), priority=1),
            _route("second", TextProvider("ok", clock), priority=2),
        ],
        RoutingPolicy.PRIORITY,
        clock,
        settings=settings,
    )
    router.record_failure("first", "network")

    assert router.select().key == "second"
    clock.advance(settings.cooldown_ms)
    assert router.select().key == "first"


def test_router_settings_reject_invalid_cooldown_and_ewma():
    with pytest.raises(ValidationError):
        RouterSettings(cooldown_ms=-1)
    with pytest.raises(ValidationError):
        RouterSettings(ewma_alpha=1.1)


async def test_failover_before_first_token_uses_next_route():
    clock = ManualClock()
    failed = FailingProvider()
    healthy = TextProvider("healthy", clock)
    router = Router(
        [
            _route("failed", failed, priority=1),
            _route("healthy", healthy, priority=2),
        ],
        RoutingPolicy.PRIORITY,
        clock,
    )
    provider = RoutingLlmProvider(router)

    events = [event async for event in provider.stream_chat(_request())]

    assert [event.text for event in events if isinstance(event, TokenDelta)] == [
        "healthy"
    ]
    assert failed.calls == 1 and healthy.calls == 1


async def test_error_stream_end_before_first_token_fails_over():
    class ErrorEndProvider:
        async def stream_chat(self, request):
            yield StreamEnd("error")

    clock = ManualClock()
    router = Router(
        [
            _route("error", ErrorEndProvider(), priority=1),
            _route("healthy", TextProvider("healthy", clock), priority=2),
        ],
        RoutingPolicy.PRIORITY,
        clock,
    )

    events = [
        event async for event in RoutingLlmProvider(router).stream_chat(_request())
    ]
    assert any(isinstance(event, TokenDelta) for event in events)


async def test_no_failover_after_first_token():
    clock = ManualClock()
    failed = FailingProvider(after_token=True)
    healthy = TextProvider("must-not-run", clock)
    router = Router(
        [
            _route("failed", failed, priority=1),
            _route("healthy", healthy, priority=2),
        ],
        RoutingPolicy.PRIORITY,
        clock,
    )

    seen = []
    with pytest.raises(RuntimeError, match="unavailable"):
        async for event in RoutingLlmProvider(router).stream_chat(_request()):
            seen.append(event)

    assert [event.text for event in seen] == ["partial"]
    assert healthy.calls == 0


async def test_all_routes_failed_raises_typed_error():
    clock = ManualClock()
    router = Router([_route("only", FailingProvider())], RoutingPolicy.PRIORITY, clock)

    with pytest.raises(AllRoutesFailed, match="only"):
        _ = [
            event async for event in RoutingLlmProvider(router).stream_chat(_request())
        ]


async def test_first_token_latency_updates_ewma():
    clock = ManualClock()
    settings = RouterSettings(ewma_alpha=0.5)
    route = _route("route", TextProvider("ok", clock, latency_ms=20))
    router = Router([route], RoutingPolicy.LATENCY, clock, settings=settings)

    _ = [event async for event in RoutingLlmProvider(router).stream_chat(_request())]
    route.provider.latency_ms = 40
    _ = [event async for event in RoutingLlmProvider(router).stream_chat(_request())]

    assert router.state("route").first_token_ewma_ms == pytest.approx(30)


async def test_routing_spans_explain_selection_fallback_and_cooldown():
    clock = ManualClock()
    router = Router(
        [
            _route("failed", FailingProvider(), priority=1),
            _route("healthy", TextProvider("ok", clock), priority=2),
        ],
        RoutingPolicy.PRIORITY,
        clock,
    )

    _ = [event async for event in RoutingLlmProvider(router).stream_chat(_request())]

    fallback = next(
        span for span in router.spans if span.name == "lucy.router.fallback"
    )
    selected = [span for span in router.spans if span.name == "lucy.router.select"]
    assert fallback.attributes["fallback_reason"] == "RuntimeError"
    assert fallback.attributes["cooldown"] == "true"
    assert selected[-1].attributes["selected_provider_key"] == "healthy"
    assert selected[-1].attributes["route_policy"] == "priority"


async def test_provider_error_text_is_not_copied_to_router_telemetry():
    class SensitiveFailure:
        async def stream_chat(self, request):
            raise RuntimeError("Bearer should-not-leak")
            if False:
                yield StreamEnd("error")

    clock = ManualClock()
    router = Router(
        [_route("route", SensitiveFailure())], RoutingPolicy.PRIORITY, clock
    )
    with pytest.raises(AllRoutesFailed) as excinfo:
        _ = [
            event async for event in RoutingLlmProvider(router).stream_chat(_request())
        ]

    serialized = repr(router.spans) + str(excinfo.value)
    assert "should-not-leak" not in serialized
