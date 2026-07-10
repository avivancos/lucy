"""Lazy provider-plugin discovery and resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Callable, Dict, Iterable, List, Optional, Union, cast

from lucy.providers import (
    Capability,
    LOCAL_PROVIDER_NAME,
    ModelInfo,
    parse_spec_string,
)
from lucy.voice import SttProvider, TtsProvider

PLUGIN_ENTRY_POINT_GROUP = "lucy.plugins"


class PluginError(Exception):
    """Base error for plugin discovery, capability, and ABI failures."""


class PluginNotFoundError(PluginError):
    """Raised when a provider spec names an undiscovered plugin."""


class PluginCapabilityError(PluginError):
    """Raised when a plugin has no factory for a requested capability."""


class PluginAbiError(PluginError):
    """Raised when an entry point or factory result violates the frozen ABI."""


@dataclass(frozen=True)
class LucyPlugin:
    name: str
    version: str
    capabilities: List[Capability]
    catalog: List[ModelInfo]
    stt_factory: Optional[Callable[[str], SttProvider]] = None
    tts_factory: Optional[Callable[[str], TtsProvider]] = None
    llm_factory: Optional[Callable[[str], object]] = None
    realtime_factory: Optional[Callable[[str], object]] = None
    embedding_factory: Optional[Callable[[str], object]] = None


def _entry_points(group: str) -> List[metadata.EntryPoint]:
    try:
        return list(metadata.entry_points(group=group))
    except TypeError:
        discovered = metadata.entry_points()
        selected: Iterable[metadata.EntryPoint]
        if hasattr(discovered, "select"):
            selected = discovered.select(group=group)  # type: ignore[attr-defined]
        else:
            legacy = cast(Dict[str, Iterable[metadata.EntryPoint]], discovered)
            selected = legacy.get(group, ())
        return list(cast(Iterable[metadata.EntryPoint], selected))


class PluginRegistry:
    def __init__(self, points: Dict[str, metadata.EntryPoint]) -> None:
        self._points = dict(points)
        self._loaded: Dict[str, LucyPlugin] = {}

    @classmethod
    def discover(cls, group: str = PLUGIN_ENTRY_POINT_GROUP) -> "PluginRegistry":
        return cls({point.name: point for point in _entry_points(group)})

    def names(self) -> List[str]:
        return sorted(self._points)

    def loaded_names(self) -> List[str]:
        return sorted(self._loaded)

    def get(self, name: str) -> LucyPlugin:
        cached = self._loaded.get(name)
        if cached is not None:
            return cached
        point = self._points.get(name)
        if point is None:
            available = sorted({*self._points, LOCAL_PROVIDER_NAME})
            raise PluginNotFoundError(
                "plugin %r not found; available plugins: %s"
                % (name, ", ".join(available))
            )
        loaded = point.load()
        if not isinstance(loaded, LucyPlugin):
            raise PluginAbiError("entry point %r did not load a LucyPlugin" % name)
        self._loaded[name] = loaded
        return loaded

    def load_all(self) -> List[LucyPlugin]:
        return [self.get(name) for name in self.names()]

    def resolve_stt(self, spec: str) -> SttProvider:
        return cast(SttProvider, self._resolve(spec, Capability.STT))

    def resolve_tts(self, spec: str) -> TtsProvider:
        return cast(TtsProvider, self._resolve(spec, Capability.TTS))

    def _resolve(
        self, spec: str, capability: Capability
    ) -> Union[SttProvider, TtsProvider]:
        plugin_name, model = parse_spec_string(spec)
        expected: object
        if capability is Capability.STT:
            expected = SttProvider
        elif capability is Capability.TTS:
            expected = TtsProvider
        else:
            raise PluginCapabilityError(
                "unsupported plugin capability: %s" % capability.value
            )

        if plugin_name == LOCAL_PROVIDER_NAME:
            from lucy.testing import LocalSttSimulator, LocalTtsSimulator

            provider: object = (
                LocalSttSimulator()
                if capability is Capability.STT
                else LocalTtsSimulator()
            )
        else:
            plugin = self.get(plugin_name)
            factory = (
                plugin.stt_factory
                if capability is Capability.STT
                else plugin.tts_factory
            )
            if factory is None:
                raise PluginCapabilityError(
                    "plugin %r has no %s factory" % (plugin_name, capability.value)
                )
            assert model is not None
            provider = factory(model)

        if not isinstance(provider, expected):
            raise PluginAbiError(
                "plugin %r returned an invalid %s provider"
                % (plugin_name, capability.value)
            )
        return cast(Union[SttProvider, TtsProvider], provider)


def load_plugins(group: str = PLUGIN_ENTRY_POINT_GROUP) -> PluginRegistry:
    return PluginRegistry.discover(group)
