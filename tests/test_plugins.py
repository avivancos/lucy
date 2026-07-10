from dataclasses import FrozenInstanceError, fields
from importlib.metadata import entry_points
import sys

import pytest

from lucy.agent import VoiceAgent
from lucy.plugins import (
    LucyPlugin,
    PLUGIN_ENTRY_POINT_GROUP,
    PluginRegistry,
    PluginAbiError,
    PluginCapabilityError,
    PluginError,
    PluginNotFoundError,
    load_plugins,
)
from lucy.providers import (
    Capability,
    InvalidProviderSpecError,
    ModelCatalogConflictError,
    ModelInfo,
    default_model_registry,
    parse_spec_string,
    revalidate_model_registry,
)
from lucy.specs import AgentSpec, LucySpec, VoiceSpec
from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.voice import AudioChunk, SttProvider, TtsProvider


def test_simulators_satisfy_runtime_checkable_provider_protocols():
    assert isinstance(LocalSttSimulator(), SttProvider)
    assert isinstance(LocalTtsSimulator(), TtsProvider)
    assert not isinstance(object(), SttProvider)
    assert not isinstance(object(), TtsProvider)


def test_parse_spec_string_accepts_local_and_plugin_model():
    assert parse_spec_string("local") == ("local", None)
    assert parse_spec_string("deepgram/nova-3") == ("deepgram", "nova-3")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "deepgram",
        "deepgram/",
        "/nova-3",
        "plugin/   ",
        "   /model",
        "a/b/c",
    ],
)
def test_parse_spec_string_rejects_malformed_values(value):
    with pytest.raises(InvalidProviderSpecError) as excinfo:
        parse_spec_string(value)

    message = str(excinfo.value)
    assert repr(value) in message
    assert "local" in message
    assert "<plugin>/<model>" in message


def test_lucy_plugin_is_frozen_with_optional_factories():
    plugin = LucyPlugin(
        name="fixture",
        version="0.1.0",
        capabilities=[Capability.STT],
        catalog=[],
    )

    assert [item.name for item in fields(plugin)] == [
        "name",
        "version",
        "capabilities",
        "catalog",
        "stt_factory",
        "tts_factory",
        "llm_factory",
        "realtime_factory",
        "embedding_factory",
    ]
    assert plugin.stt_factory is None
    assert plugin.tts_factory is None
    assert plugin.llm_factory is None
    assert plugin.realtime_factory is None
    assert plugin.embedding_factory is None
    with pytest.raises(FrozenInstanceError):
        plugin.name = "changed"


def test_plugin_errors_share_public_base_class():
    assert issubclass(PluginNotFoundError, PluginError)
    assert issubclass(PluginCapabilityError, PluginError)
    assert issubclass(PluginAbiError, PluginError)


def test_fixture_plugin_entry_points_discoverable():
    discovered = entry_points()
    if hasattr(discovered, "select"):
        points = discovered.select(group=PLUGIN_ENTRY_POINT_GROUP)
    else:
        points = discovered.get(PLUGIN_ENTRY_POINT_GROUP, [])

    assert {point.name for point in points} >= {"fixture", "fixture-broken"}


def test_discovery_does_not_import_plugin_module():
    sys.modules.pop("lucy_fixture_plugin", None)

    registry = load_plugins()

    assert "fixture" in registry.names()
    assert "lucy_fixture_plugin" not in sys.modules

    registry.get("fixture")
    assert "lucy_fixture_plugin" in sys.modules
    assert registry.loaded_names() == ["fixture"]


def test_get_loads_caches_and_returns_same_plugin():
    registry = PluginRegistry.discover()

    first = registry.get("fixture")
    second = registry.get("fixture")

    assert first is second
    assert registry.loaded_names() == ["fixture"]


def test_unknown_plugin_raises_with_available_names():
    registry = load_plugins()

    with pytest.raises(PluginNotFoundError) as excinfo:
        registry.get("missing")

    message = str(excinfo.value)
    assert "missing" in message
    assert "fixture" in message
    assert "fixture-broken" in message
    assert "local" in message


def test_local_and_plugin_specs_resolve_through_one_code_path():
    registry = load_plugins()

    assert isinstance(registry.resolve_stt("local"), LocalSttSimulator)
    assert isinstance(registry.resolve_tts("local"), LocalTtsSimulator)
    assert isinstance(registry.resolve_stt("fixture/echo-1"), SttProvider)
    assert isinstance(registry.resolve_tts("fixture/echo-1"), TtsProvider)


def test_broken_plugin_factory_raises_plugin_abi_error():
    with pytest.raises(PluginAbiError, match="fixture-broken"):
        load_plugins().resolve_stt("fixture-broken/x")


def test_missing_capability_factory_raises_capability_error():
    with pytest.raises(PluginCapabilityError, match="tts"):
        load_plugins().resolve_tts("fixture-broken/x")


def test_default_registry_merges_plugin_catalogs():
    merged = default_model_registry(load_plugins().load_all())

    assert merged.get("fixture", "echo-1") is not None


def test_catalog_conflict_raises_named_error():
    duplicate = LucyPlugin(
        name="duplicate",
        version="0.1.0",
        capabilities=[Capability.STT],
        catalog=[
            ModelInfo(
                provider="openai",
                model="whisper-1",
                capabilities=[Capability.STT],
                recommended_for=[],
            )
        ],
    )

    with pytest.raises(ModelCatalogConflictError, match="openai/whisper-1"):
        default_model_registry([duplicate])


def test_revalidation_detects_removed_plugin_model():
    merged = default_model_registry(load_plugins().load_all())
    core_only = default_model_registry()

    report = revalidate_model_registry(merged, core_only)

    assert "fixture/echo-1" in report.removed


def _voice_spec(stt: str, tts: str) -> LucySpec:
    return LucySpec(
        agent=AgentSpec(name="Plugin agent", goal="Help", prompt="Be helpful"),
        voice=VoiceSpec(transport="sim", stt_provider=stt, tts_provider=tts),
    )


async def test_voice_agent_runs_turn_with_plugin_spec_strings():
    agent = VoiceAgent(
        _voice_spec("fixture/echo-1", "local"),
        plugins=load_plugins(),
    )
    session = agent.start_session("plugin-session")

    events = await session.user_audio(
        AudioChunk(session_id="plugin-session", data=b"hello", sequence=0)
    )
    assert any(getattr(event, "text", None) == "hello" for event in events)
    assert session.last_response == "You said: hello"

    await session.synthesize(session.last_response)
    session.close()


def test_voice_agent_unknown_plugin_error_names_available():
    with pytest.raises(PluginNotFoundError) as excinfo:
        VoiceAgent(
            _voice_spec("missing/model", "local"),
            plugins=load_plugins(),
        )

    message = str(excinfo.value)
    assert "missing" in message
    assert "fixture" in message
    assert "local" in message
