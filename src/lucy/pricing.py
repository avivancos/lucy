"""Typed in-process voice pricing and raw usage attribution (ADR 0015)."""

from __future__ import annotations

import os
import stat
from enum import Enum
from pathlib import Path
from typing import Annotated, Dict

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucy.metrics import CostBreakdown, MIN_BILLABLE_AUDIO_MINUTES
from lucy.limits import (
    MAX_CONTROL_DURATION_MS,
    MAX_PRICE_RATE,
    MAX_PRICEBOOK_BYTES,
    MAX_PRICEBOOK_NAME_LENGTH,
    MAX_PRICEBOOK_VERSION_LENGTH,
    MAX_USAGE_UNITS,
)

UNPRICED_PRICEBOOK_VERSION = "unpriced"
NonEmptyString = Annotated[
    str, Field(min_length=1, max_length=MAX_PRICEBOOK_VERSION_LENGTH)
]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
NonNegativeFinite = Annotated[
    float, Field(ge=0.0, le=MAX_PRICE_RATE, allow_inf_nan=False)
]
NonNegativeUsageInt = Annotated[int, Field(strict=True, ge=0, le=MAX_USAGE_UNITS)]
NonNegativeDurationInt = Annotated[
    int, Field(strict=True, ge=0, le=MAX_CONTROL_DURATION_MS)
]


class TelephonyDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class VoiceUsage(BaseModel):
    """Provider-reported or control-channel-derived billable usage facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    llm_prompt_tokens: NonNegativeUsageInt = 0
    llm_cached_prompt_tokens: NonNegativeUsageInt = 0
    llm_completion_tokens: NonNegativeUsageInt = 0
    stt_audio_ms: NonNegativeDurationInt = 0
    tts_characters: NonNegativeUsageInt = 0
    tts_audio_ms: NonNegativeDurationInt = 0
    telephony_minutes: NonNegativeFinite = 0.0
    telephony_direction: TelephonyDirection = TelephonyDirection.INBOUND
    rag_requests: NonNegativeUsageInt = 0
    mcp_tool_calls: NonNegativeUsageInt = 0
    infra_minutes: NonNegativeFinite = 0.0

    @model_validator(mode="after")
    def validate_cached_tokens(self) -> "VoiceUsage":
        if self.llm_cached_prompt_tokens > self.llm_prompt_tokens:
            raise ValueError("cached prompt tokens cannot exceed prompt tokens")
        return self


class PricedVoiceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cost: CostBreakdown
    attribution: Dict[str, float]


class PriceBook(BaseModel):
    """Prices for one selected voice stack, expressed in one currency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: NonEmptyString
    currency: CurrencyCode = "USD"
    llm_prompt_per_1k: NonNegativeFinite = 0.0
    llm_cached_prompt_per_1k: NonNegativeFinite = 0.0
    llm_completion_per_1k: NonNegativeFinite = 0.0
    stt_per_minute: NonNegativeFinite = 0.0
    tts_per_1k_characters: NonNegativeFinite = 0.0
    tts_per_second: NonNegativeFinite = 0.0
    telephony_inbound_per_minute: NonNegativeFinite = 0.0
    telephony_outbound_per_minute: NonNegativeFinite = 0.0
    rag_per_request: NonNegativeFinite = 0.0
    mcp_per_call: NonNegativeFinite = 0.0
    infra_per_minute: NonNegativeFinite = 0.0

    @model_validator(mode="after")
    def validate_tts_basis(self) -> "PriceBook":
        if self.tts_per_1k_characters > 0 and self.tts_per_second > 0:
            raise ValueError("configure only one TTS billing basis")
        return self

    def calculate(self, usage: VoiceUsage) -> PricedVoiceUsage:
        stt_minutes = usage.stt_audio_ms / 60_000.0
        tts_seconds = usage.tts_audio_ms / 1_000.0
        uncached_prompt_tokens = (
            usage.llm_prompt_tokens - usage.llm_cached_prompt_tokens
        )
        llm_cost = (
            uncached_prompt_tokens / 1_000.0 * self.llm_prompt_per_1k
            + usage.llm_cached_prompt_tokens / 1_000.0 * self.llm_cached_prompt_per_1k
            + usage.llm_completion_tokens / 1_000.0 * self.llm_completion_per_1k
        )
        if self.tts_per_1k_characters > 0:
            tts_cost = usage.tts_characters / 1_000.0 * self.tts_per_1k_characters
        else:
            tts_cost = tts_seconds * self.tts_per_second
        telephony_rate = (
            self.telephony_inbound_per_minute
            if usage.telephony_direction == TelephonyDirection.INBOUND
            else self.telephony_outbound_per_minute
        )
        billable_minutes = max(
            usage.telephony_minutes,
            stt_minutes,
            tts_seconds / 60.0,
            MIN_BILLABLE_AUDIO_MINUTES,
        )
        cost = CostBreakdown(
            stt_cost=stt_minutes * self.stt_per_minute,
            llm_cost=llm_cost,
            tts_cost=tts_cost,
            telephony_cost=usage.telephony_minutes * telephony_rate,
            rag_cost=usage.rag_requests * self.rag_per_request,
            mcp_tool_cost=usage.mcp_tool_calls * self.mcp_per_call,
            infra_cost=usage.infra_minutes * self.infra_per_minute,
            billable_audio_minutes=billable_minutes,
        )
        return PricedVoiceUsage(
            cost=cost,
            attribution={
                "llm_prompt_tokens": float(usage.llm_prompt_tokens),
                "llm_cached_prompt_tokens": float(usage.llm_cached_prompt_tokens),
                "llm_completion_tokens": float(usage.llm_completion_tokens),
                "stt_audio_minutes": stt_minutes,
                "tts_characters": float(usage.tts_characters),
                "tts_audio_seconds": tts_seconds,
                "telephony_minutes": usage.telephony_minutes,
                "rag_requests": float(usage.rag_requests),
                "mcp_tool_calls": float(usage.mcp_tool_calls),
                "infra_minutes": usage.infra_minutes,
            },
        )


class PricingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_PRICING_", extra="ignore")

    pricebook_path: Path | None = None


def load_pricebook(settings: PricingSettings | None = None) -> PriceBook:
    configured = settings or PricingSettings()
    if configured.pricebook_path is None:
        return PriceBook(version=UNPRICED_PRICEBOOK_VERSION)
    return _load_pricebook_path(str(configured.pricebook_path.absolute()))


def _load_pricebook_path(path: str) -> PriceBook:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("price book path must identify a regular file")
        if metadata.st_size > MAX_PRICEBOOK_BYTES:
            raise ValueError("price book exceeds the configured size limit")
        payload = os.read(descriptor, MAX_PRICEBOOK_BYTES + 1)
        if len(payload) > MAX_PRICEBOOK_BYTES:
            raise ValueError("price book exceeds the configured size limit")
    finally:
        os.close(descriptor)
    return PriceBook.model_validate_json(payload, strict=True)


class PriceBookRegistry:
    def __init__(self) -> None:
        self._books: Dict[str, PriceBook] = {}

    def register(self, name: str, pricebook: PriceBook) -> None:
        if not name or name != name.strip() or len(name) > MAX_PRICEBOOK_NAME_LENGTH:
            raise ValueError("price book name must be non-empty and trimmed")
        if name in self._books:
            raise ValueError("price book %r is already registered" % name)
        self._books[name] = pricebook

    def require(self, name: str) -> PriceBook:
        try:
            return self._books[name]
        except KeyError as exc:
            raise KeyError("price book %r is not registered" % name) from exc
