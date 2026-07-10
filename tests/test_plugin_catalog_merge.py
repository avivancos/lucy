from lucy.plugins import load_plugins
from lucy.providers import default_model_registry


def _provider_plugins():
    registry = load_plugins()
    return [registry.get(name) for name in ("deepgram", "elevenlabs", "openai")]


def test_merged_registry_has_no_duplicate_provider_model_keys():
    merged = default_model_registry(_provider_plugins())
    keys = [(model.provider, model.model) for model in merged.models]

    assert len(keys) == len(set(keys))


def test_plugin_catalog_rows_survive_merge():
    plugins = _provider_plugins()
    merged = default_model_registry(plugins)

    for plugin in plugins:
        for expected in plugin.catalog:
            actual = merged.get(expected.provider, expected.model)
            assert actual == expected

    assert merged.get("deepgram", "nova-3") is not None
    assert merged.get("elevenlabs", "flash-v2.5") is not None
    assert merged.get("openai", "gpt-realtime") is not None
