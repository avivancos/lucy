# Getting started

Build your first Lucy voice agent offline, inspect local traces, then
switch to real providers when you are ready.

## Prerequisites

- Python >= 3.9
- A clone of this repository (the public PyPI name lands with the naming
  milestone in [ADR 0010](../adr/0010-open-core-split.md))

## Install

From the repository root:

```bash
pip install -e ".[dev]"
```

## Your first agent

A `LucySpec` names the agent and the voice stack. With `"local"`
providers, Lucy resolves deterministic in-process simulators — no API
keys and no network.

Build the spec, construct a `VoiceAgent`, open a session, feed one
audio chunk, print the final transcript, and synthesize a reply:

```python
import asyncio
from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec

async def main():
    # Spec: agent identity + local voice providers.
    spec = LucySpec(
        agent=AgentSpec(name="Quickstart Agent", goal="Help.", prompt="Be helpful."),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    # Agent: wires STT/TTS plugins and a default responder graph.
    agent = VoiceAgent(spec)
    # Session: one conversation handle with turn-shaped APIs.
    with agent.start_session("s1") as session:
        for event in await session.user_audio(
            AudioChunk(session_id="s1", data=b"I want to book a demo.", sequence=0)
        ):
            # Transcript: LocalSttSimulator UTF-8-decodes the audio bytes.
            if getattr(event, "is_final", False):
                print("caller:", event.text)
        await session.synthesize(session.last_response or "Hello!")

asyncio.run(main())
```

## See your traces

Point `LUCY_TRACE_FILE` at a JSONL path and open the local viewer:

```bash
export LUCY_TRACE_FILE=/tmp/lucy-trace.jsonl
python -m lucy.serve.devviewer
```

The viewer reads that JSONL file only. Scope cap (ADR 0010): no
storage, no auth, no cross-run comparisons, no audio.

## Switch to real providers

Provider specs use `"local"` or `"<plugin>/<model>"`. With the Deepgram
plugin installed, a string such as `deepgram/nova-3` selects that model.
When `DEEPGRAM_API_KEY` (or the matching key for another plugin) is
unset, the factory emits a `UserWarning` naming the missing env var and
falls back to a local simulator so quickstarts never break.

## Next steps

- [Providers and plugins](./providers-and-plugins.md)
- [Agent graphs](./agent-graphs.md)
- [Observability](./observability.md)
- [Testing without mocks](./testing-without-mocks.md)
- [Transports and telephony](./transports-and-telephony.md)
