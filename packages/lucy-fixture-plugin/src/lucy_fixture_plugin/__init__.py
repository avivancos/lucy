"""Installed provider plugin used to verify Lucy's public plugin ABI."""

from lucy.plugins import LucyPlugin
from lucy.providers import Capability, ModelInfo
from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.voice import SttProvider, TtsProvider


def make_stt(model: str) -> SttProvider:
    return LocalSttSimulator()


def make_tts(model: str) -> TtsProvider:
    return LocalTtsSimulator()


plugin = LucyPlugin(
    name="fixture",
    version="0.1.0",
    capabilities=[Capability.STT, Capability.TTS],
    catalog=[
        ModelInfo(
            provider="fixture",
            model="echo-1",
            capabilities=[Capability.STT, Capability.TTS],
            recommended_for=["plugin contract tests"],
            low_latency=True,
        )
    ],
    stt_factory=make_stt,
    tts_factory=make_tts,
)
