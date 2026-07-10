"""OpenAI models supported by Lucy's LLM and realtime plugin."""

from lucy.providers import Capability, ModelInfo

MODELS = [
    ModelInfo(
        provider="openai",
        model="gpt-realtime",
        capabilities=[Capability.REALTIME, Capability.LLM, Capability.TTS],
        recommended_for=["native speech-to-speech", "low-latency agents"],
        low_latency=True,
    ),
    ModelInfo(
        provider="openai",
        model="gpt-5",
        capabilities=[Capability.LLM],
        recommended_for=["high-quality chained reasoning"],
    ),
    ModelInfo(
        provider="openai",
        model="gpt-5-mini",
        capabilities=[Capability.LLM],
        recommended_for=["fast cost-efficient chained reasoning"],
        low_latency=True,
    ),
]
