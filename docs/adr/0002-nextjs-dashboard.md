# ADR 0002 - Next.js Dashboard

## Status

Accepted

## Context

Lucy needs a serious operations dashboard for live calls, traces, sentiment,
funnel state, RAG inspection, provider costs, and deployment health.

## Decision

The Lucy dashboard is built with React, Next.js App Router, and TypeScript. It
consumes the FastAPI OpenAPI contract through a generated typed client and uses
realtime channels for live operational views.

Live calls, traces, latency, sentiment, funnel state, and cost updates are
delivered from FastAPI through WebSocket or SSE channels.

## Consequences

- FastAPI remains the source of truth for API contracts.
- Dashboard state is organized around typed API responses and realtime events.
- UI implementation must be visually audited before task closure.
