"""Typed latency budgets (ADR 0011, agents.md typed-config invariant).

Every timing threshold the runtime enforces lives here, never inline. Override
any field from the environment with the ``LUCY_BUDGET_`` prefix, e.g.
``LUCY_BUDGET_TURN_TOTAL_MS=500``.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class LatencyBudgets(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_BUDGET_", extra="ignore")

    endpoint_silence_ms: int = 150  # trailing silence that ends the caller's turn
    stt_final_ms: int = 60  # partial -> final settle time
    control_transport_ms: int = 10  # one control-channel hop
    graph_dispatch_ms: int = 10  # dispatch a turn into the graph runtime
    llm_first_clause_ms: int = 380  # first speakable clause from the LLM
    tts_first_byte_ms: int = 150  # first audio byte from TTS
    gateway_pacing_ms: int = 30  # gateway playback pacing granularity
    turn_total_ms: int = 800  # end-to-end p50 turn budget
    max_tool_rounds_per_turn: int = 3  # tool-call rounds before forcing a reply


class LlmPricing(BaseSettings):
    """Per-1k-token prices for cost accounting. Zero defaults - no invented
    prices; override from the environment with the ``LUCY_LLM_PRICE_`` prefix."""

    model_config = SettingsConfigDict(env_prefix="LUCY_LLM_PRICE_", extra="ignore")

    prompt_per_1k: float = 0.0
    completion_per_1k: float = 0.0
