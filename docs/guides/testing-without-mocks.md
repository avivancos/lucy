# Testing without mocks

Lucy follows a no mocks policy: tests use real local implementations.
This guide is the practitioner view of ADR 0003.

## The policy

ADR 0003 bans mocking frameworks and invented provider behavior. A test
that patches a network client or fabricates a provider response to make
an assertion pass is out of policy.

What is sanctioned: real local implementations, deterministic in-process
simulators that implement the same Protocols, local protocol servers,
and recorded fixtures from real interactions. See
[ADR 0003](../adr/0003-no-mocks-testing-policy.md).

## The simulator inventory

Public names exported by `lucy.testing`:

- `LocalSttSimulator` — UTF-8 audio bytes become transcript events
- `LocalTtsSimulator` — text becomes started/chunk/finished TTS events
- `LocalMcpCommandTransport` — in-process MCP command transport
- `LocalEmbeddingFixture` — deterministic embedding vectors
- `InMemoryOtelSpanExporter` — capture OTel spans in memory
- `InMemoryTraceExporter` — capture Lucy telemetry events in memory
- `LocalMetricEventChannel` — in-process metric event channel
- `LocalRealtimeSimulator` — scripted realtime session turns
- `RecordingBlobStoreSimulator` — local recording blob store
- `ScriptedRealtimeToolCall` — one scripted realtime tool call
- `ScriptedRealtimeTurn` — one scripted realtime turn
- `check_checkpoint_store` — shared checkpoint-store contract helper

## Your first no-mocks test

Drive STT and TTS simulators through one round trip:

```python
import asyncio

from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.voice import AudioChunk

async def main():
    stt = LocalSttSimulator()
    tts = LocalTtsSimulator()
    events = await stt.transcribe(
        [AudioChunk(session_id="s", data=b"hello there", sequence=0)]
    )
    assert events[-1].is_final
    assert events[-1].text == "hello there"
    spoken = await tts.synthesize("s", "hello there")
    assert spoken[0].status == "started"
    assert spoken[-1].status == "finished"
    print("round trip ok:", events[-1].text)

asyncio.run(main())
```

## Deterministic time

Inject `ManualClock` instead of sleeping on the wall clock. Pending
`sleep` calls resolve when you `advance(ms)`:

```python
import asyncio

from lucy.clock import ManualClock

async def main():
    clock = ManualClock()

    async def sleeper():
        await clock.sleep(0.5)
        print("woke")

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0)
    clock.advance(500)
    await task

asyncio.run(main())
```

## Scenario harness

`ConversationHarness` plus `booking_happy_path()` runs an end-to-end
agent call on `LocalGatewaySimulator` with zero keys. Pass a responder
or a `TurnDriver`, optionally sharing a `ManualClock`. The full
runnable session example is in
[Transports and telephony](./transports-and-telephony.md).

## For plugin authors

Reuse the contract suites, `ReplayTransport`, and the
`python -m lucy.testing.record` CLI documented in
[Providers and plugins](./providers-and-plugins.md). Do not duplicate
those recipes here — keep one source of truth for plugin ABI proofs.
