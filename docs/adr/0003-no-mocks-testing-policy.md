# ADR 0003 - No-Mocks Testing Policy

## Status

Accepted

## Context

Lucy is infrastructure for production voice agents. Tests that replace important
behavior with mocks can make latency, streaming, permissions, provider
boundaries, and cost accounting look safer than they are.

## Decision

Lucy is a no-mocks project. Tests must exercise real local implementations,
deterministic in-process simulators, local protocol servers, or recorded
fixtures from real interactions. Mocking frameworks and invented provider
behavior are not used to make tests pass.

## Consequences

- Provider contracts are tested through local simulators that implement the same
  adapter boundary as production providers.
- MCP tests use local protocol transports or local MCP servers.
- External paid/network providers are reserved for explicit integration profiles,
  not default automated tests.
- TDD still applies: write the failing test first, but the test must target a
  real local behavior boundary.

