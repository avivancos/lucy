# Observability

Lucy emits client-side telemetry through `lucy.observe`. Privacy and
sampling run in open code before anything leaves the process. The wire
contract is [`../telemetry-wire-v1.md`](../telemetry-wire-v1.md).

## One call to configure

```python
from lucy.observe import configure

tracer = configure()
```

With no arguments and no env overrides, `configure()` returns a
console-exporting tracer. Explicit keyword arguments win over
environment variables. Setting `LUCY_TRACING=0` yields a disabled
no-op tracer.

## Exporters

Built-in exporters:

- `ConsoleExporter` — human-readable turn waterfalls
- `JsonlFileExporter` — one wire-shaped JSON line per event
- `OtlpBridgeExporter` — bridge onto an OpenTelemetry span exporter
- `InMemoryTraceExporter` — in-process capture for tests (`lucy.testing`)

Third-party exporters register under the `lucy.exporters` entry-point
group (the `lucy-cloud` package uses this for cloud export).

```python
import asyncio
import tempfile
from pathlib import Path

from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec
from lucy.observe import JsonlFileExporter, configure

async def main():
    trace_dir = Path(tempfile.mkdtemp())
    path = trace_dir / "trace.jsonl"
    tracer = configure(exporters=[JsonlFileExporter(path)])
    spec = LucySpec(
        agent=AgentSpec(name="Trace Agent", goal="Help.", prompt="Be helpful."),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    agent = VoiceAgent(spec, tracer=tracer)
    with agent.start_session("s1") as session:
        await session.user_audio(
            AudioChunk(session_id="s1", data=b"I want to book a demo.", sequence=0)
        )
        await session.synthesize(session.last_response or "Hello!")
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    print("jsonl events:", len(lines))
    assert len(lines) > 0

asyncio.run(main())
```

## Environment variables

Copied from the wire spec:

| variable | effect |
| --- | --- |
| `LUCY_TRACING` | enable/disable (default: enabled, console exporter) |
| `LUCY_ENDPOINT` | required credential-free HTTPS ingest base URL; HTTP only for loopback development |
| `LUCY_API_KEY` | enables the cloud exporter when installed |
| `LUCY_PROJECT` | project name in the envelope |
| `LUCY_TRACE_SAMPLE` | session sample rate 0..1 |
| `LUCY_TRACE_FILE` | JSONL file exporter path |

## Privacy, client-side

- `redact_pii` defaults to `true`
- `record_audio` defaults to `false`
- `trace_sample_rate` / `LUCY_TRACE_SAMPLE` gates sessions
- transcripts kill-switch (`transcripts=false` on the wire) drops
  transcript events wholesale

All of these are enforced in open code before anything leaves the
process ([ADR 0010](../adr/0010-open-core-split.md)).

## The local trace viewer

```bash
export LUCY_TRACE_FILE=/tmp/lucy-trace.jsonl
python -m lucy.serve.devviewer
```

Scope cap (ADR 0010): no storage, no auth, no cross-run comparisons, no audio.

## Cloud export

Install `lucy-cloud` and set `LUCY_API_KEY` (plus `LUCY_ENDPOINT` for
self-hosted ingest). The package auto-attaches through the
`lucy.exporters` entry point — no code changes. Delivery is fail-open:
telemetry never crashes a call. The normative contract is
[`../telemetry-wire-v1.md`](../telemetry-wire-v1.md); this guide does
not copy the wire schema.
