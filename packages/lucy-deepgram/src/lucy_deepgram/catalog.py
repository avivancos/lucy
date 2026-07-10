"""Deepgram models supported by the Lucy STT plugin."""

from lucy.providers import Capability, ModelInfo

MODELS = [
    ModelInfo(
        provider="deepgram",
        model="flux",
        capabilities=[Capability.STT],
        recommended_for=["voice-agent turn-taking"],
        low_latency=True,
    ),
    ModelInfo(
        provider="deepgram",
        model="nova-3",
        capabilities=[Capability.STT],
        recommended_for=["accurate streaming transcription"],
        low_latency=True,
    ),
]
