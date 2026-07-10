"""Deepgram provider plugin for Lucy."""

import warnings

from lucy.plugins import LucyPlugin
from lucy.providers import Capability
from lucy.testing import LocalSttSimulator
from lucy.voice import SttProvider

from lucy_deepgram.catalog import MODELS
from lucy_deepgram.settings import DeepgramSettings


def stt_factory(model: str) -> SttProvider:
    settings = DeepgramSettings()
    if settings.api_key is None or not settings.api_key.get_secret_value():
        warnings.warn(
            "DEEPGRAM_API_KEY is not set; using LocalSttSimulator",
            UserWarning,
            stacklevel=2,
        )
        return LocalSttSimulator()

    from lucy_deepgram.stt import DeepgramSttAdapter

    return DeepgramSttAdapter(model=model, settings=settings)


PLUGIN = LucyPlugin(
    name="deepgram",
    version="0.1.0",
    capabilities=[Capability.STT],
    catalog=MODELS,
    stt_factory=stt_factory,
)
