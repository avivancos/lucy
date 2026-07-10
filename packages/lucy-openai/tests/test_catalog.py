from lucy.plugins import load_plugins
from lucy.providers import Capability


def test_plugin_discovered_with_llm_and_realtime_catalog():
    plugin = load_plugins().get("openai")

    assert plugin.name == "openai"
    assert plugin.capabilities == [Capability.LLM, Capability.REALTIME]
    assert [model.model for model in plugin.catalog] == [
        "gpt-realtime",
        "gpt-5",
        "gpt-5-mini",
    ]
