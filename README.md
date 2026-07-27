# Lucy

[![CI](https://github.com/avivancos/lucy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/avivancos/lucy/actions/workflows/ci.yml)

Lucy is an open-source, Python-first SDK for production voice agents.
It is telephony-native (PBX and SIP first, not CPaaS-only; see
[ADR 0012](docs/adr/0012-telephony-native-first.md)) and licensed under
Apache-2.0. The closed platform (ops dashboard, fleet control) builds on
this SDK in a separate repo ([ADR 0010](docs/adr/0010-open-core-split.md)).

## Install

Clone the repo and install in editable mode with the development extra
(Python >= 3.9):

```bash
pip install -e ".[dev]"
```

The public PyPI distribution name lands with the naming milestone
([ADR 0010](docs/adr/0010-open-core-split.md)); until then, install from
source. The import name stays `lucy`.

## Quickstart

Build a voice agent with local providers, zero API keys, and zero
network. The same code lives in
[`examples/quickstart_voice_agent.py`](examples/quickstart_voice_agent.py):

```python
import asyncio
from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec

async def main():
    spec = LucySpec(
        agent=AgentSpec(name="Quickstart Agent", goal="Help.", prompt="Be helpful."),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    agent = VoiceAgent(spec)
    with agent.start_session("s1") as session:
        for event in await session.user_audio(
            AudioChunk(session_id="s1", data=b"I want to book a demo.", sequence=0)
        ):
            if getattr(event, "is_final", False):
                print("caller:", event.text)
        await session.synthesize(session.last_response or "Hello!")

asyncio.run(main())
```

## From console traces to the cloud

Install the open `lucy-cloud` client, set one env var, and re-run the
same quickstart with zero code changes:

```bash
pip install -e ./packages/lucy-cloud
export LUCY_API_KEY=...
# optional for self-hosted ingest:
# export LUCY_ENDPOINT=https://ingest.example.com
```

Telemetry is fail-open: it never adds latency or crashes a call. The
normative wire contract is
[`docs/telemetry-wire-v1.md`](docs/telemetry-wire-v1.md).

## Guides

- [Getting started](docs/guides/getting-started.md) — install, first
  agent, local traces, then real providers.
- [Providers and plugins](docs/guides/providers-and-plugins.md) — spec
  strings, `LucyPlugin`, contract suites, fixture recording.
- [Agent graphs](docs/guides/agent-graphs.md) — `AgentGraph`,
  checkpoints, prebuilt nodes and graphs.
- [Observability](docs/guides/observability.md) — `configure()`,
  exporters, privacy, local viewer, cloud export.
- [Testing without mocks](docs/guides/testing-without-mocks.md) —
  simulators, `ManualClock`, harness scenarios (ADR 0003).
- [Transports and telephony](docs/guides/transports-and-telephony.md) —
  control channel, AudioSocket, CPaaS, SIP trunk.

## Architecture

Lucy splits an event/cognition plane from a media plane
([ADR 0011](docs/adr/0011-hybrid-streaming-voice-runtime.md)): Python
owns control events and agent graphs; the Rust media gateway owns the
audio socket and forks audio to STT/TTS so frames never cross into
Python ([ADR 0004](docs/adr/0004-ultra-low-latency-telephony-media-plane.md)).
Anything that runs in the user's process is open; storage, aggregation,
and cross-run comparison are closed
([ADR 0010](docs/adr/0010-open-core-split.md)).

## Local development

Docker Compose is the sanctioned runtime:

```bash
docker compose up --build
docker compose run --rm lucy-api pytest
```

On a prepared venv:

```bash
pip install -e ".[dev]"
pytest
uvicorn lucy.serve.app:create_app --factory --reload
```

`lucy.serve.app:create_app` is the framework serving runtime (health,
metrics, realtime SSE, models, evals). The FastAPI developer portal is
at `/docs`, ReDoc at `/redoc`, and the OpenAPI contract at
`/openapi.json`.

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
