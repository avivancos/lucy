"""In-process provider-instance routing (ADR 0015)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Callable, Dict, List, Optional, Sequence, Set, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucy.clock import Clock
from lucy.llm import LlmProvider, LlmRequest, LlmStreamEvent, StreamEnd, TokenDelta
from lucy.tracing import Span


class RoutingPolicy(str, Enum):
    PRIORITY = "priority"
    WEIGHTED = "weighted"
    LATENCY = "latency"


class RouterSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_ROUTER_", extra="ignore")

    cooldown_ms: int = Field(default=30_000, ge=0)
    ewma_alpha: float = Field(default=0.25, gt=0.0, le=1.0)


@dataclass(frozen=True)
class Route:
    key: str
    provider: object
    priority: int = 100
    weight: float = 1.0
    low_latency: bool = False


@dataclass
class RouteState:
    cooldown_until: float = 0.0
    last_failure_reason: str = ""
    first_token_ewma_ms: Optional[float] = None


class AllRoutesFailed(RuntimeError):
    """Raised when every eligible provider fails before its first token."""


class Router:
    def __init__(
        self,
        routes: Sequence[Route],
        policy: RoutingPolicy,
        clock: Clock,
        *,
        settings: Optional[RouterSettings] = None,
        random_value: Callable[[], float] = random.random,
        emit_span: Optional[Callable[[Span], None]] = None,
    ) -> None:
        if not routes:
            raise ValueError("router requires at least one route")
        keys = [route.key for route in routes]
        if len(keys) != len(set(keys)):
            raise ValueError("router route keys must be unique")
        if any(route.weight <= 0 for route in routes):
            raise ValueError("router weights must be positive")
        self.routes = list(routes)
        self.policy = policy
        self.clock = clock
        self.settings = settings or RouterSettings()
        self.random_value = random_value
        self.emit_span = emit_span
        self.spans: List[Span] = []
        self._states: Dict[str, RouteState] = {
            route.key: RouteState() for route in routes
        }
        self._span_sequence = 0

    def state(self, key: str) -> RouteState:
        try:
            return self._states[key]
        except KeyError as exc:
            raise KeyError("unknown route %r" % key) from exc

    def select(self, exclude: Optional[Set[str]] = None) -> Route:
        now = self.clock.monotonic()
        excluded = exclude or set()
        eligible = [
            route
            for route in self.routes
            if route.key not in excluded and self.state(route.key).cooldown_until <= now
        ]
        if not eligible:
            raise AllRoutesFailed("no eligible provider routes")
        if self.policy is RoutingPolicy.PRIORITY:
            selected = min(eligible, key=lambda route: (route.priority, route.key))
        elif self.policy is RoutingPolicy.WEIGHTED:
            selected = self._weighted(eligible)
        else:
            selected = min(
                eligible,
                key=lambda route: (
                    not route.low_latency,
                    self.state(route.key).first_token_ewma_ms
                    if self.state(route.key).first_token_ewma_ms is not None
                    else float("inf"),
                    route.priority,
                    route.key,
                ),
            )
        state = self.state(selected.key)
        self._span(
            "lucy.router.select",
            {
                "route_policy": self.policy.value,
                "selected_provider_key": selected.key,
                "cooldown": "false",
                "first_token_ewma_ms": (
                    ""
                    if state.first_token_ewma_ms is None
                    else str(state.first_token_ewma_ms)
                ),
            },
        )
        return selected

    def _weighted(self, eligible: Sequence[Route]) -> Route:
        total = sum(route.weight for route in eligible)
        target = min(max(self.random_value(), 0.0), 1.0) * total
        running = 0.0
        for route in eligible:
            running += route.weight
            if target < running:
                return route
        return eligible[-1]

    def record_failure(self, key: str, reason: str) -> None:
        state = self.state(key)
        state.cooldown_until = (
            self.clock.monotonic() + self.settings.cooldown_ms / 1000.0
        )
        state.last_failure_reason = reason
        self._span(
            "lucy.router.fallback",
            {
                "route_policy": self.policy.value,
                "selected_provider_key": key,
                "fallback_reason": reason,
                "cooldown": "true",
                "first_token_ewma_ms": (
                    ""
                    if state.first_token_ewma_ms is None
                    else str(state.first_token_ewma_ms)
                ),
            },
            status="error",
        )

    def record_first_token(self, key: str, latency_ms: float) -> None:
        state = self.state(key)
        previous = state.first_token_ewma_ms
        state.first_token_ewma_ms = (
            latency_ms
            if previous is None
            else self.settings.ewma_alpha * latency_ms
            + (1.0 - self.settings.ewma_alpha) * previous
        )

    def _span(
        self, name: str, attributes: Dict[str, str], *, status: str = "ok"
    ) -> None:
        self._span_sequence += 1
        now_ms = int(self.clock.monotonic() * 1000)
        span = Span(
            span_id="router-%d" % self._span_sequence,
            parent_id=None,
            name=name,
            started_at_ms=now_ms,
            ended_at_ms=now_ms,
            attributes=attributes,
            status=status,
        )
        self.spans.append(span)
        if self.emit_span is not None:
            self.emit_span(span)


class RoutingLlmProvider:
    def __init__(self, router: Router) -> None:
        self.router = router

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        attempted: Set[str] = set()
        failures: List[str] = []
        while len(attempted) < len(self.router.routes):
            try:
                route = self.router.select(attempted)
            except AllRoutesFailed:
                break
            attempted.add(route.key)
            provider = cast(LlmProvider, route.provider)
            buffered: List[LlmStreamEvent] = []
            committed = False
            started = self.router.clock.monotonic()
            try:
                async for event in provider.stream_chat(request):
                    if isinstance(event, StreamEnd) and event.finish_reason == "error":
                        raise RuntimeError("stream_error")
                    if isinstance(event, TokenDelta) and not committed:
                        committed = True
                        latency_ms = (self.router.clock.monotonic() - started) * 1000.0
                        self.router.record_first_token(route.key, latency_ms)
                        for pending in buffered:
                            yield pending
                        buffered.clear()
                        yield event
                    elif committed:
                        yield event
                    else:
                        buffered.append(event)
            except Exception as exc:
                reason = type(exc).__name__
                self.router.record_failure(route.key, reason)
                if committed:
                    raise
                failures.append("%s: %s" % (route.key, reason))
                continue
            for pending in buffered:
                yield pending
            return
        detail = "; ".join(failures) or "no eligible routes"
        raise AllRoutesFailed("all provider routes failed: %s" % detail)
