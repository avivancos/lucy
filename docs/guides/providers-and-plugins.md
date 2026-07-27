# Providers and plugins

How Lucy resolves STT/TTS/LLM providers through typed spec strings and
lazy `LucyPlugin` entry points.

## Provider spec strings

`parse_spec_string` in `lucy.providers` accepts exactly two forms:

- `"local"` — the built-in simulator path
- `"<plugin>/<model>"` — a discovered plugin and model id

Anything else raises `InvalidProviderSpecError`.

```python
from lucy.providers import InvalidProviderSpecError, parse_spec_string

assert parse_spec_string("local") == ("local", None)
assert parse_spec_string("deepgram/nova-3") == ("deepgram", "nova-3")
try:
    parse_spec_string("not-a-spec")
except InvalidProviderSpecError as exc:
    print("rejected:", exc)
```

## Anatomy of a plugin

`LucyPlugin` is a frozen dataclass. Field order is part of the ABI:

`name`, `version`, `capabilities`, `catalog`, `stt_factory`,
`tts_factory`, `llm_factory`, `realtime_factory`, `embedding_factory`.

Plugins register under the `lucy.plugins` entry-point group.
`load_plugins()` discovers entry points lazily; loading a plugin can
raise `PluginNotFoundError`, `PluginCapabilityError`, or
`PluginAbiError`.

## Write a plugin, step by step

Package layout:

```text
lucy_acme/
  __init__.py      # PLUGIN = LucyPlugin(...)
  stt.py
pyproject.toml
```

Entry-point declaration:

```toml
[project.entry-points."lucy.plugins"]
acme = "lucy_acme:PLUGIN"
```

Construct a plugin whose factories return `lucy.testing` simulators and
assert the STT result satisfies the runtime-checkable `SttProvider`
Protocol:

```python
import asyncio

from lucy import SttProvider
from lucy.plugins import LucyPlugin
from lucy.providers import Capability, ModelInfo
from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.voice import AudioChunk

def stt_factory(model: str) -> SttProvider:
    return LocalSttSimulator()

def tts_factory(model: str):
    return LocalTtsSimulator()

plugin = LucyPlugin(
    name="acme",
    version="0.1.0",
    capabilities=[Capability.STT, Capability.TTS],
    catalog=[
        ModelInfo(
            provider="acme",
            model="echo-1",
            capabilities=[Capability.STT, Capability.TTS],
            recommended_for=["offline demos"],
        )
    ],
    stt_factory=stt_factory,
    tts_factory=tts_factory,
)

provider = plugin.stt_factory("echo-1")
assert isinstance(provider, SttProvider)

async def main():
    events = await provider.transcribe(
        [AudioChunk(session_id="s", data=b"hello", sequence=0)]
    )
    assert events and events[-1].is_final
    print("stt ok:", events[-1].text)

asyncio.run(main())
```

## Catalog rows

Each `ModelInfo` carries `provider`, `model`, `capabilities`,
`recommended_for`, plus optional `low_latency` and `notes`.
`default_model_registry(plugins=...)` merges plugin catalogs into the
core registry; conflicting provider/model keys raise
`ModelCatalogConflictError`.

## Prove it with the contract suites

`SttContractSuite`, `TtsContractSuite`, and `LlmContractSuite` in
`lucy.testing.contracts` check streaming shape, stall timeouts,
cancellation without orphan tasks, and malformed-payload errors.
Subclass and implement the hooks on real simulators:

```python
from typing import List, Tuple

from lucy.testing import LocalSttSimulator
from lucy.testing.contracts import SttContractSuite
from lucy.voice import AudioChunk, SttProvider

class DemoSttSuite(SttContractSuite):
    def make_provider(self) -> SttProvider:
        return LocalSttSimulator()

    def chunks(self) -> List[AudioChunk]:
        return [AudioChunk(session_id="s", data=b"hello", sequence=0)]

    def make_stalled_provider(self) -> SttProvider:
        return LocalSttSimulator(delay_ms=60_000)

    def make_malformed_case(self) -> Tuple[SttProvider, List[AudioChunk]]:
        return (
            LocalSttSimulator(),
            [AudioChunk(session_id="s", data=b"\xff\xfe", sequence=0)],
        )

suite = DemoSttSuite()
assert suite.make_provider() is not None
print("hooks ok")
```

## Record real fixtures

Record against a live plugin, then replay without credentials:

```bash
python -m lucy.testing.record --plugin deepgram --capability stt -m live
```

`ReplayTransport` plays back scrubbed frames. The recorder strips
configured secrets and masks volatile fields (`request_id`, `event_id`,
and related ids). Use `-m live` only when you intend a real network
recording; offline tests inject `ReplayTransport` instead.

## Keyless behavior

Plugin factories fall back to simulators with a `UserWarning` that names
the missing env var (for example `DEEPGRAM_API_KEY`). Quickstarts never
break when keys are absent.
