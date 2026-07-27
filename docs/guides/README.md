# Guides

Public Lucy SDK guides. Each guide ships at least one runnable Python
block exercised by `tests/test_docs_guides.py`.

| Guide | What it covers |
| --- | --- |
| [Getting started](./getting-started.md) | Install, first agent, local traces, real providers |
| [Providers and plugins](./providers-and-plugins.md) | Spec strings, `LucyPlugin`, contracts, recording |
| [Agent graphs](./agent-graphs.md) | `AgentGraph`, checkpoints, prebuilt catalog |
| [Observability](./observability.md) | `configure()`, exporters, privacy, cloud export |
| [Testing without mocks](./testing-without-mocks.md) | ADR 0003, simulators, harness, `ManualClock` |
| [Transports and telephony](./transports-and-telephony.md) | Control channel, AudioSocket, CPaaS, SIP trunk |

A docs site generator (mkdocs or similar) is intentionally out of scope
for launch; these markdown files are the public surface.
