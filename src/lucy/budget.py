"""In-process cost budgets and LLM rate limits (ADR 0015)."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, AsyncIterator, Deque, Dict, Optional

from pydantic import BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucy.clock import Clock
from lucy.limits import (
    MAX_BUDGET_LEDGER_USD,
    MAX_BUDGET_IDENTITY_LENGTH,
    MAX_BUDGET_IDENTITIES,
    MAX_BUDGET_RATE_LIMIT,
    MAX_USAGE_UNITS,
)
from lucy.llm import LlmProvider, LlmRequest, LlmStreamEvent, StreamEnd, UsageReport
from lucy.observe import Tracer, get_tracer
from lucy.pricing import PriceBook, VoiceUsage

RATE_WINDOW_SECONDS = 60.0
BUDGET_CURRENCY = "USD"


def _parse_rate_limit(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("rate limits must be integers, not booleans")
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("rate limits must be integers") from exc
    if not isinstance(value, int):
        raise ValueError("rate limits must be integers")
    return value


RateLimit = Annotated[
    int,
    BeforeValidator(_parse_rate_limit),
    Field(ge=1, le=MAX_BUDGET_RATE_LIMIT),
]


def _parse_usd_limit(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("USD limits must be numbers, not booleans")
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError("USD limits must be numbers") from exc
    if not isinstance(value, (int, float)):
        raise ValueError("USD limits must be numbers")
    return value


UsdLimit = Annotated[
    float,
    BeforeValidator(_parse_usd_limit),
    Field(ge=0, le=MAX_USAGE_UNITS, allow_inf_nan=False),
]


class BudgetScope(str, Enum):
    SESSION = "session"
    AGENT = "agent"
    RPM = "rpm"
    TPM = "tpm"


class BudgetLevel(str, Enum):
    SOFT = "soft"
    HARD = "hard"


class BudgetPolicy(BaseSettings):
    """Typed local policy; omitted limits disable their respective checks."""

    model_config = SettingsConfigDict(env_prefix="LUCY_GOVERNANCE_", extra="ignore")

    session_soft_usd: Optional[UsdLimit] = None
    session_hard_usd: Optional[UsdLimit] = None
    agent_soft_usd: Optional[UsdLimit] = None
    agent_hard_usd: Optional[UsdLimit] = None
    soft_requests_per_minute: Optional[RateLimit] = None
    hard_requests_per_minute: Optional[RateLimit] = None
    soft_tokens_per_minute: Optional[RateLimit] = None
    hard_tokens_per_minute: Optional[RateLimit] = None

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "BudgetPolicy":
        pairs = (
            ("session", self.session_soft_usd, self.session_hard_usd),
            ("agent", self.agent_soft_usd, self.agent_hard_usd),
            (
                "RPM",
                self.soft_requests_per_minute,
                self.hard_requests_per_minute,
            ),
            ("TPM", self.soft_tokens_per_minute, self.hard_tokens_per_minute),
        )
        for label, soft, hard in pairs:
            if soft is not None and hard is not None and soft > hard:
                raise ValueError("%s soft limit cannot exceed hard limit" % label)
        return self

    @property
    def has_usd_limits(self) -> bool:
        return any(
            limit is not None
            for limit in (
                self.session_soft_usd,
                self.session_hard_usd,
                self.agent_soft_usd,
                self.agent_hard_usd,
            )
        )

    @property
    def has_tpm_limits(self) -> bool:
        return (
            self.soft_tokens_per_minute is not None
            or self.hard_tokens_per_minute is not None
        )

    @property
    def has_rpm_limits(self) -> bool:
        return (
            self.soft_requests_per_minute is not None
            or self.hard_requests_per_minute is not None
        )

    @property
    def has_agent_usd_limits(self) -> bool:
        return self.agent_soft_usd is not None or self.agent_hard_usd is not None

    @property
    def has_session_usd_limits(self) -> bool:
        return self.session_soft_usd is not None or self.session_hard_usd is not None


@dataclass(frozen=True)
class BudgetNotice:
    scope: BudgetScope
    level: BudgetLevel
    current: float
    limit: float
    session_id: str


class BudgetExceeded(RuntimeError):
    """Typed hard action used by non-call LLM consumers."""

    def __init__(
        self, notice: BudgetNotice, *, usage: Optional[UsageReport] = None
    ) -> None:
        super().__init__(
            "%s hard budget action: current %.6f, limit %.6f"
            % (notice.scope.value, notice.current, notice.limit)
        )
        self.notice = notice
        self.usage = usage


class BudgetMeteringError(RuntimeError):
    """A governed provider completed without the required usage report."""


@dataclass(frozen=True)
class BudgetLease:
    """Opaque capability granting lifecycle ownership of one budget session."""

    _session_id: str = field(repr=False)
    _agent_id: str = field(repr=False)
    _token: object = field(repr=False)


class BudgetController:
    """Shared deterministic ledger for one or more local voice sessions."""

    def __init__(
        self, policy: BudgetPolicy, clock: Clock, *, tracer: Optional[Tracer] = None
    ) -> None:
        self.policy = policy
        self.clock = clock
        self._default_tracer = tracer
        self._session_spend: Dict[str, float] = defaultdict(float)
        self._agent_spend: Dict[str, float] = defaultdict(float)
        self._requests: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=MAX_BUDGET_RATE_LIMIT)
        )
        self._tokens: Dict[str, Deque[tuple[float, int]]] = defaultdict(
            lambda: deque(maxlen=MAX_BUDGET_RATE_LIMIT)
        )
        self._token_totals: Dict[str, int] = defaultdict(int)
        self._session_leases: Dict[str, BudgetLease] = {}
        self._poisoned_leases: set[BudgetLease] = set()
        self._emitted: set[tuple[BudgetScope, str]] = set()
        self._session_tracers: Dict[str, Tracer] = {}

    def open_session(
        self,
        session_id: str,
        agent_id: str,
        *,
        tracer: Optional[Tracer] = None,
    ) -> BudgetLease:
        """Validate and reserve bounded state for a session/agent pair."""
        self._validate_identity("session_id", session_id)
        self._validate_identity("agent_id", agent_id)
        if session_id in self._session_leases:
            raise ValueError("budget session is already active")
        self._retire_expired_rate_state()
        self._register_identities(session_id, agent_id)
        lease = BudgetLease(session_id, agent_id, object())
        self._session_leases[session_id] = lease
        if tracer is not None:
            self._session_tracers[session_id] = tracer
        return lease

    def validate_currency(self, currency: str) -> None:
        if self.policy.has_usd_limits and currency != BUDGET_CURRENCY:
            raise ValueError("USD budget requires a USD price book")

    def record_cost(
        self, lease: BudgetLease, amount_usd: float
    ) -> tuple[BudgetNotice, ...]:
        self._validate_cost(amount_usd)
        session_id, agent_id = self._validate_lease(lease)
        return self._add_cost(session_id, agent_id, float(amount_usd))

    def _add_cost(
        self, session_id: str, agent_id: str, amount_usd: float
    ) -> tuple[BudgetNotice, ...]:
        self._session_spend[session_id] = self._bounded_add(
            self._session_spend[session_id], amount_usd
        )
        if self.policy.has_agent_usd_limits:
            self._agent_spend[agent_id] = self._bounded_add(
                self._agent_spend[agent_id], amount_usd
            )
        return self._emit(self._current_cost_notices(session_id, agent_id))

    def preflight_usd(self, lease: BudgetLease) -> tuple[BudgetNotice, ...]:
        """Check current USD ledgers without changing spend."""
        session_id, agent_id = self._validate_lease(lease)
        return self._emit(self._current_cost_notices(session_id, agent_id))

    def _current_cost_notices(
        self, session_id: str, agent_id: str
    ) -> list[BudgetNotice]:
        notices: list[BudgetNotice] = []
        if self.policy.has_session_usd_limits:
            notices.extend(
                self._threshold_notices(
                    BudgetScope.SESSION,
                    session_id,
                    self._session_spend.get(session_id, 0.0),
                    self.policy.session_soft_usd,
                    self.policy.session_hard_usd,
                    session_id,
                )
            )
        if self.policy.has_agent_usd_limits:
            notices.extend(
                self._threshold_notices(
                    BudgetScope.AGENT,
                    agent_id,
                    self._agent_spend[agent_id],
                    self.policy.agent_soft_usd,
                    self.policy.agent_hard_usd,
                    session_id,
                )
            )
        return notices

    @staticmethod
    def _validate_cost(amount_usd: float) -> None:
        if (
            isinstance(amount_usd, bool)
            or not isinstance(amount_usd, (int, float))
            or not math.isfinite(amount_usd)
            or amount_usd < 0
        ):
            raise ValueError("amount_usd must be a finite nonnegative number")

    def begin_request(self, lease: BudgetLease) -> tuple[BudgetNotice, ...]:
        session_id, agent_id = self._validate_lease(lease)
        now = self.clock.monotonic()
        self._prune(agent_id, now)

        projected_requests = len(self._requests.get(agent_id, ())) + 1
        notices = self._current_cost_notices(session_id, agent_id)
        if any(notice.level == BudgetLevel.HARD for notice in notices):
            return self._emit(notices)
        notices.extend(
            self._threshold_notices(
                BudgetScope.RPM,
                agent_id,
                float(projected_requests),
                self.policy.soft_requests_per_minute,
                self.policy.hard_requests_per_minute,
                session_id,
            )
        )
        token_total = self._token_totals.get(agent_id, 0)
        if (
            self.policy.hard_tokens_per_minute is not None
            and token_total >= self.policy.hard_tokens_per_minute
        ):
            notices.extend(
                self._notice_once(
                    BudgetScope.TPM,
                    BudgetLevel.HARD,
                    agent_id,
                    float(token_total),
                    float(self.policy.hard_tokens_per_minute),
                    session_id,
                )
            )
        if self.policy.has_rpm_limits and not any(
            notice.level == BudgetLevel.HARD for notice in notices
        ):
            self._requests[agent_id].append(now)
        return self._emit(notices)

    def record_tokens(
        self, lease: BudgetLease, usage: UsageReport
    ) -> tuple[BudgetNotice, ...]:
        session_id, agent_id = self._validate_lease(lease)
        now = self.clock.monotonic()
        self._prune(agent_id, now)
        token_count = usage.prompt_tokens + usage.completion_tokens
        current_total = self._token_totals.get(agent_id, 0)
        projected_total = current_total + token_count
        if self.policy.has_tpm_limits and token_count > 0:
            tokens = self._tokens[agent_id]
            if len(tokens) == MAX_BUDGET_RATE_LIMIT:
                _, evicted = tokens.popleft()
                current_total -= evicted
            tokens.append((now, token_count))
            self._token_totals[agent_id] = current_total + token_count
        notices = self._threshold_notices(
            BudgetScope.TPM,
            agent_id,
            float(projected_total),
            self.policy.soft_tokens_per_minute,
            self.policy.hard_tokens_per_minute,
            session_id,
        )
        return self._emit(notices)

    def close_session(self, lease: BudgetLease) -> None:
        """Release session-scoped state after its final cost check."""
        if not isinstance(lease, BudgetLease):
            raise ValueError("invalid or stale budget lease")
        session_id = lease._session_id
        if self._session_leases.get(session_id) != lease:
            raise ValueError("invalid or stale budget lease")
        self._session_spend.pop(session_id, None)
        agent_id = lease._agent_id
        self._session_leases.pop(session_id, None)
        self._poisoned_leases.discard(lease)
        self._session_tracers.pop(session_id, None)
        self._emitted.discard((BudgetScope.SESSION, session_id))
        self._retire_agent_rate_state(agent_id)

    def _threshold_notices(
        self,
        scope: BudgetScope,
        key: str,
        current: float,
        soft: Optional[float],
        hard: Optional[float],
        session_id: str,
    ) -> tuple[BudgetNotice, ...]:
        notices: list[BudgetNotice] = []
        if soft is not None and current >= soft:
            notices.extend(
                self._notice_once(
                    scope,
                    BudgetLevel.SOFT,
                    key,
                    current,
                    float(soft),
                    session_id,
                )
            )
        if hard is not None and current > hard:
            notices.extend(
                self._notice_once(
                    scope,
                    BudgetLevel.HARD,
                    key,
                    current,
                    float(hard),
                    session_id,
                )
            )
        return tuple(notices)

    def _notice_once(
        self,
        scope: BudgetScope,
        level: BudgetLevel,
        key: str,
        current: float,
        limit: float,
        session_id: str,
    ) -> tuple[BudgetNotice, ...]:
        identity = (scope, key)
        if level == BudgetLevel.SOFT and identity in self._emitted:
            return ()
        if level == BudgetLevel.SOFT:
            self._emitted.add(identity)
        return (
            BudgetNotice(
                scope=scope,
                level=level,
                current=current,
                limit=limit,
                session_id=session_id,
            ),
        )

    def _prune(self, agent_id: str, now: float) -> None:
        cutoff = now - RATE_WINDOW_SECONDS
        requests = self._requests.get(agent_id, deque())
        while requests and requests[0] <= cutoff:
            requests.popleft()
        tokens = self._tokens.get(agent_id, deque())
        while tokens and tokens[0][0] <= cutoff:
            _, expired = tokens.popleft()
            self._token_totals[agent_id] -= expired
        if (
            self.policy.soft_requests_per_minute is None
            or len(requests) < self.policy.soft_requests_per_minute
        ):
            self._reset_rate_notices(BudgetScope.RPM, agent_id)
        token_total = self._token_totals.get(agent_id, 0)
        if (
            self.policy.soft_tokens_per_minute is None
            or token_total < self.policy.soft_tokens_per_minute
        ):
            self._reset_rate_notices(BudgetScope.TPM, agent_id)

    def _reset_rate_notices(self, scope: BudgetScope, agent_id: str) -> None:
        self._emitted.discard((scope, agent_id))

    def _emit(
        self, notices: list[BudgetNotice] | tuple[BudgetNotice, ...]
    ) -> tuple[BudgetNotice, ...]:
        result = tuple(notices)
        for notice in result:
            self._emit_notice(notice)
        return result

    def _emit_notice(self, notice: BudgetNotice) -> None:
        tracer = self._session_tracers.get(notice.session_id)
        if tracer is None:
            tracer = self._default_tracer or get_tracer()
        if not tracer.enabled:
            return
        at_ms = max(0, int(self.clock.monotonic() * 1000))
        try:
            tracer.span(
                session_id=notice.session_id,
                turn_id=None,
                span_id=tracer.new_id(),
                name="budget.check",
                status=("error" if notice.level == BudgetLevel.HARD else "fallback"),
                started_at_ms=at_ms,
                ended_at_ms=at_ms,
                attributes={
                    "budget.scope": notice.scope.value,
                    "budget.level": notice.level.value,
                    "budget.current": "%.6f" % notice.current,
                    "budget.limit": "%.6f" % notice.limit,
                },
            )
        except Exception:
            # Budget enforcement remains available when observability fails.
            return

    @staticmethod
    def _validate_identity(name: str, value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > MAX_BUDGET_IDENTITY_LENGTH
        ):
            raise ValueError("%s must be non-empty, trimmed, and bounded" % name)

    def _register_identities(self, session_id: str, agent_id: str) -> None:
        if (
            session_id not in self._session_leases
            and len(self._session_leases) >= MAX_BUDGET_IDENTITIES
        ):
            raise ValueError("session budget identity capacity exceeded")
        known_agents = self._known_agents()
        if agent_id not in known_agents and len(known_agents) >= MAX_BUDGET_IDENTITIES:
            raise ValueError("agent budget identity capacity exceeded")
        if self.policy.has_agent_usd_limits:
            self._agent_spend.setdefault(agent_id, 0.0)

    def _validate_lease(self, lease: BudgetLease) -> tuple[str, str]:
        if not isinstance(lease, BudgetLease):
            raise ValueError("invalid or stale budget lease")
        if self._session_leases.get(lease._session_id) != lease:
            raise ValueError("invalid or stale budget lease")
        if lease in self._poisoned_leases:
            raise BudgetMeteringError("budget session metering is poisoned")
        return lease._session_id, lease._agent_id

    def poison(self, lease: BudgetLease) -> None:
        self._validate_lease(lease)
        self._poisoned_leases.add(lease)

    def _known_agents(self) -> set[str]:
        return (
            {lease._agent_id for lease in self._session_leases.values()}
            | set(self._agent_spend)
            | set(self._requests)
            | set(self._tokens)
        )

    def _retire_expired_rate_state(self) -> None:
        now = self.clock.monotonic()
        for agent_id in set(self._requests) | set(self._tokens):
            self._prune(agent_id, now)
            self._retire_agent_rate_state(agent_id)

    def _retire_agent_rate_state(self, agent_id: str) -> None:
        if any(lease._agent_id == agent_id for lease in self._session_leases.values()):
            return
        requests = self._requests.get(agent_id)
        tokens = self._tokens.get(agent_id)
        if not requests:
            self._requests.pop(agent_id, None)
            self._emitted.discard((BudgetScope.RPM, agent_id))
        if not tokens:
            self._tokens.pop(agent_id, None)
            self._token_totals.pop(agent_id, None)
            self._emitted.discard((BudgetScope.TPM, agent_id))

    @staticmethod
    def _bounded_add(current: float, incoming: float) -> float:
        return min(MAX_BUDGET_LEDGER_USD, current + incoming)


class BudgetedLlmProvider:
    """LLM decorator enforcing rate limits and optional priced USD budgets."""

    def __init__(
        self,
        inner: LlmProvider,
        controller: BudgetController,
        *,
        session_id: str,
        agent_id: str,
        pricebook: Optional[PriceBook] = None,
    ) -> None:
        self._inner = inner
        self._controller = controller
        self._session_id = session_id
        self._agent_id = agent_id
        self._pricebook = pricebook
        self._lease: Optional[BudgetLease] = None
        self._voice_session_owns_cost = False
        if pricebook is not None:
            controller.validate_currency(pricebook.currency)

    @property
    def budget_binding(self) -> "BudgetBinding":
        return BudgetBinding(
            controller=self._controller,
            session_id=self._session_id,
            agent_id=self._agent_id,
            pricebook=self._pricebook,
            owner=self,
        )

    def open_budget_session(
        self,
        *,
        tracer: Optional[Tracer] = None,
        voice_session_owns_cost: bool = False,
    ) -> BudgetLease:
        if self._lease is None:
            self._lease = self._controller.open_session(
                self._session_id, self._agent_id, tracer=tracer
            )
            self._voice_session_owns_cost = voice_session_owns_cost
        elif self._voice_session_owns_cost != voice_session_owns_cost:
            raise BudgetMeteringError("budget lease already has a different cost owner")
        return self._lease

    def close_budget_session(self) -> None:
        if self._lease is None:
            return
        lease = self._lease
        self._lease = None
        self._voice_session_owns_cost = False
        self._controller.close_session(lease)

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if (
            self._controller.policy.has_usd_limits
            and self._pricebook is None
            and not self._voice_session_owns_cost
        ):
            raise BudgetMeteringError("standalone USD governance requires a price book")
        lease = self.open_budget_session(
            voice_session_owns_cost=self._voice_session_owns_cost
        )
        notices = self._controller.begin_request(lease)
        self._raise_hard(notices)
        stream = self._inner.stream_chat(request)
        primary_error: Optional[BaseException] = None
        usage_seen = False
        requires_usage = (
            self._controller.policy.has_tpm_limits
            or self._controller.policy.has_usd_limits
        )
        try:
            async for event in stream:
                if isinstance(event, UsageReport):
                    usage_seen = True
                    usage_notices = list(self._controller.record_tokens(lease, event))
                    if self._pricebook is not None:
                        priced = self._pricebook.calculate(
                            VoiceUsage(
                                llm_prompt_tokens=event.prompt_tokens,
                                llm_cached_prompt_tokens=event.cached_prompt_tokens,
                                llm_completion_tokens=event.completion_tokens,
                            )
                        )
                        usage_notices.extend(
                            self._controller.record_cost(lease, priced.cost.llm_cost)
                        )
                    self._raise_hard(tuple(usage_notices), usage=event)
                elif isinstance(event, StreamEnd) and requires_usage and not usage_seen:
                    raise BudgetMeteringError(
                        "governed LLM stream ended without a usage report"
                    )
                yield event
            if requires_usage and not usage_seen:
                raise BudgetMeteringError(
                    "governed LLM stream ended without a usage report"
                )
        except BaseException as exc:
            primary_error = exc
            if requires_usage and not usage_seen:
                self._poison_unmetered(lease)
            raise
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await close()
                except BaseException as cleanup_error:
                    if requires_usage and not usage_seen:
                        self._poison_unmetered(lease)
                    if primary_error is None:
                        raise
                    primary_error.__context__ = cleanup_error

    def _poison_unmetered(self, lease: BudgetLease) -> None:
        try:
            self._controller.poison(lease)
        except (BudgetMeteringError, ValueError):
            return

    @staticmethod
    def _raise_hard(
        notices: tuple[BudgetNotice, ...], *, usage: Optional[UsageReport] = None
    ) -> None:
        hard = next(
            (notice for notice in notices if notice.level == BudgetLevel.HARD), None
        )
        if hard is not None:
            raise BudgetExceeded(hard, usage=usage)


@dataclass(frozen=True)
class BudgetBinding:
    controller: BudgetController
    session_id: str
    agent_id: str
    pricebook: Optional[PriceBook]
    owner: BudgetedLlmProvider
