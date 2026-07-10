"""OpenAI provider plugin for Lucy."""

import warnings

from lucy.clock import MonotonicClock
from lucy.llm import LocalLlmSimulator, ScriptedLlmTurn, UsageReport
from lucy.plugins import LucyPlugin
from lucy.providers import Capability

from lucy_openai.catalog import MODELS
from lucy_openai.settings import OpenAiSettings

LOCAL_RESPONSE = "Local provider response."


def llm_factory(model: str) -> object:
    settings = OpenAiSettings()
    if settings.api_key is None or not settings.api_key.get_secret_value():
        warnings.warn(
            "OPENAI_API_KEY is not set; using LocalLlmSimulator",
            UserWarning,
            stacklevel=2,
        )
        return LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=[LOCAL_RESPONSE],
                    usage=UsageReport(prompt_tokens=0, completion_tokens=0),
                )
            ],
            MonotonicClock(),
            token_interval_ms=0,
        )

    from lucy_openai.llm import OpenAiLlmAdapter

    return OpenAiLlmAdapter(model=model, settings=settings)


def realtime_factory(model: str) -> object:
    settings = OpenAiSettings()
    if settings.api_key is None or not settings.api_key.get_secret_value():
        raise RuntimeError("OPENAI_API_KEY is required for realtime")

    from lucy_openai.realtime import OpenAiRealtimeAdapter

    return OpenAiRealtimeAdapter(model=model, settings=settings)


PLUGIN = LucyPlugin(
    name="openai",
    version="0.1.0",
    capabilities=[Capability.LLM, Capability.REALTIME],
    catalog=MODELS,
    llm_factory=llm_factory,
    realtime_factory=realtime_factory,
)
