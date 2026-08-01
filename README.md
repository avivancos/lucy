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
uvicorn lucy.api.app:create_app --factory --reload
```

The FastAPI developer portal is available at `/docs`, ReDoc at `/redoc`, and the
OpenAPI contract at `/openapi.json`.

