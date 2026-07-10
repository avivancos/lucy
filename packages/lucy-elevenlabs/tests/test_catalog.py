import pytest

from lucy.plugins import load_plugins
from lucy.providers import Capability
from lucy.testing import LocalTtsSimulator
from lucy_elevenlabs import tts_factory


def test_plugin_discovered_with_tts_catalog():
    plugin = load_plugins().get("elevenlabs")

    assert plugin.name == "elevenlabs"
    assert plugin.capabilities == [Capability.TTS]
    assert [model.model for model in plugin.catalog] == [
        "flash-v2.5",
        "turbo-v2.5",
    ]


def test_factory_without_key_warns_and_returns_simulator():
    with pytest.warns(UserWarning, match="ELEVENLABS_API_KEY"):
        provider = tts_factory("flash-v2.5")

    assert isinstance(provider, LocalTtsSimulator)
