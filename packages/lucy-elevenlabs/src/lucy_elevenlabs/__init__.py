"""ElevenLabs provider plugin for Lucy."""

import warnings

from lucy.plugins import LucyPlugin
from lucy.providers import Capability
from lucy.testing import LocalTtsSimulator
from lucy.voice import TtsProvider

from lucy_elevenlabs.catalog import MODELS
from lucy_elevenlabs.settings import ElevenLabsSettings


def tts_factory(model: str) -> TtsProvider:
    settings = ElevenLabsSettings()
    if settings.api_key is None or not settings.api_key.get_secret_value():
        warnings.warn(
            "ELEVENLABS_API_KEY is not set; using LocalTtsSimulator",
            UserWarning,
            stacklevel=2,
        )
        return LocalTtsSimulator()

    from lucy_elevenlabs.tts import ElevenLabsTtsAdapter

    return ElevenLabsTtsAdapter(model=model, settings=settings)


PLUGIN = LucyPlugin(
    name="elevenlabs",
    version="0.1.0",
    capabilities=[Capability.TTS],
    catalog=MODELS,
    tts_factory=tts_factory,
)
