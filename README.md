# Lucy

Lucy is a Python-first framework and platform foundation for production voice
agents. It is designed around ultra-low-latency voice pipelines, MCP-first
integrations, CRM-ready metrics, synthetic evaluations, and an operations-grade
dashboard.

## Architecture

- Python + FastAPI control plane.
- Async Python graph runtime for the first multi-node executor.
- React + Next.js dashboard.
- MCP-first external integrations.
- Docker-first local environment.
- Optional Rust media gateway sidecar for future audio hot paths.

## Quickstart

Build a voice agent in a few lines, with no API keys and no network - provider
strings resolve to deterministic local simulators:

```python
import asyncio
from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec

async def main():
    spec = LucySpec(
        agent=AgentSpec(name="Quickstart Agent", goal="Help the caller.", prompt="Be helpful."),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    agent = VoiceAgent(spec)
    with agent.start_session("s1") as session:
        await session.user_audio(AudioChunk(session_id="s1", data=b"hello there", sequence=0))
        await session.synthesize(session.last_response or "Hello!")

asyncio.run(main())
```

A runnable version is in [`examples/quickstart_voice_agent.py`](examples/quickstart_voice_agent.py).
Everything advertised in `lucy.__all__` is the supported public surface; real
provider plugins resolve through the same string seam in a later milestone.

## Primary Metric

```text
cost_per_minute =
  (stt_cost + llm_cost + tts_cost + telephony_cost + rag_cost + mcp_tool_cost + infra_cost)
  / billable_audio_minutes
```

## Local Development

The intended runtime is Docker Compose:

```bash
docker compose up --build
```

For backend-only development on a prepared Python environment:

```bash
pip install -e ".[dev]"
pytest
uvicorn lucy.serve.app:create_app --factory --reload
```

`lucy.serve.app:create_app` is the framework serving runtime (health, metrics,
realtime SSE, models, evals). The legacy `lucy.api.app` entrypoint still works
but is deprecated; it additionally mounts the fleet and Pili routes until they
move to their own repos.

The FastAPI developer portal is available at `/docs`, ReDoc at `/redoc`, and the
OpenAPI contract at `/openapi.json`.

