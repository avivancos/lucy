# ADR 0001 - Python First, Rust For Measured Hot Paths

## Status

Accepted

## Context

Lucy needs product velocity on specs, MCP integrations, CRM workflows, RAG,
evals, and observability. Most voice-agent latency is dominated by network
transport and external STT, LLM, and TTS providers, while media processing and
high-concurrency streaming can become p99 bottlenecks.

## Decision

Lucy starts as a Python-first voice-agent framework and FastAPI platform. Rust is
reserved for performance-sensitive media and streaming sidecars after
instrumentation proves a bottleneck or concurrency limit.

## Consequences

- Python owns the control plane, runtime v0, specs, registry, MCP, metrics, RAG,
  evals, and CRM model.
- Rust sidecars may own media gateway, WebRTC/SIP routing, jitter buffers, VAD,
  frame processing, backpressure, and high-concurrency sessions.
- The boundary between Python and Rust must remain explicit and observable.
