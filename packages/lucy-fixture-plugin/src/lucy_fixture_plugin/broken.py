"""Intentionally invalid provider returned by a real installed plugin."""

from lucy.plugins import LucyPlugin
from lucy.providers import Capability


class NotAProvider:
    pass


def make_stt(model: str) -> object:
    return NotAProvider()


plugin = LucyPlugin(
    name="fixture-broken",
    version="0.1.0",
    capabilities=[Capability.STT],
    catalog=[],
    stt_factory=make_stt,  # type: ignore[arg-type]
)
