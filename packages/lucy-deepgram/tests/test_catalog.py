import pytest

from lucy.plugins import load_plugins
from lucy.providers import Capability
from lucy.testing import LocalSttSimulator
from lucy_deepgram import stt_factory


def test_plugin_discovered_with_stt_catalog():
    plugin = load_plugins().get("deepgram")

    assert plugin.name == "deepgram"
    assert plugin.capabilities == [Capability.STT]
    assert [model.model for model in plugin.catalog] == ["flux", "nova-3"]


def test_factory_without_key_warns_and_returns_simulator():
    with pytest.warns(UserWarning, match="DEEPGRAM_API_KEY"):
        provider = stt_factory("nova-3")

    assert isinstance(provider, LocalSttSimulator)
