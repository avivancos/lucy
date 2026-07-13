# 55 - Add SRE service observability (Prometheus RED/USE + readiness)

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Observability (SRE)
**Estimated effort:** ~6 h
**Depends on:** 24, 25
**State:** pending

## Goal

Make the serving runtime observable as a *service*, not just as a stream of call
telemetry: a Prometheus exposition endpoint with RED (rate/errors/duration) and USE
(utilization/saturation/errors) instruments for the runtime's own behaviour, plus
readiness and liveness probes distinct from the basic health check. This is what an
operator scrapes to know Lucy itself is healthy, separate from how any single call
went.

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - operating rules (English, no mocks, no hardcoded ports/paths/
  thresholds outside typed settings).
- `docs/adr/0013-analytics-and-sre-observability.md` (card 52) - records that
  in-process service metrics are OPEN while hosted/multi-tenant observability is
  closed; this card delivers the open SRE seam.
- `src/lucy/serve/app.py` (card 23) - the `create_app` factory and the EXISTING
  `GET /metrics` route, which returns *business* JSON (primary metric, latency_p95,
  sentiment, funnel). That route MUST NOT change; the Prometheus endpoint is a new,
  separate path.
- `src/lucy/observe/__init__.py` and `src/lucy/observe/exporters.py` (card 24) - the
  bounded export queue with its drop counter and the `TraceExporter` protocol; the
  USE metrics read the queue depth and drop counter from here.
- `src/lucy/runtime.py` - `GraphExecutor` per-turn execution (deadlines, retries,
  fallbacks); the RED turn/LLM/tool counters and the deadline/fallback counters are
  sourced from its execution path (instrumented in card 25).
- `src/lucy/session.py` - `VoiceSession`; the active-session gauge counts live
  sessions here.
- `tests/test_api.py` - house style: plain pytest + `TestClient`, no mocks.

## Spec

Add `prometheus-client` to `pyproject.toml` dependencies. New module
`src/lucy/serve/prometheus.py` owning a single `CollectorRegistry` and the
instrument definitions; `create_app` mounts the exposition route and the probes.

### Typed settings (no hardcoded paths/ports)

- `ServiceMetricsSettings(BaseSettings)` with `env_prefix="LUCY_METRICS_"`:
  - `prometheus_path: str = "/metrics/prometheus"` (the exposition path; default
    avoids the existing JSON `/metrics`).
  - `namespace: str = "lucy"` (metric name prefix).

### Instruments (registered on the module registry; namespace `lucy`)

RED (request/work rate, errors, duration):

- `lucy_http_requests_total{route,method,status}` - Counter.
- `lucy_http_request_duration_seconds{route,method}` - Histogram.
- `lucy_turn_duration_seconds` - Histogram of per-turn processing time.
- `lucy_turns_total{outcome}` - Counter (`outcome` in ok/fallback/error/cancelled).
- `lucy_llm_calls_total{status}` and `lucy_tool_calls_total{status}` - Counters.

USE (utilization, saturation, errors):

- `lucy_active_sessions` - Gauge of live VoiceSessions.
- `lucy_event_queue_depth` and `lucy_exporter_queue_depth` - Gauges.
- `lucy_exporter_dropped_total` - Counter mirroring card 24's drop counter.
- `lucy_provider_timeouts_total{stage}` and `lucy_provider_fallbacks_total{stage}` -
  Counters (`stage` in stt/llm/tts/rag/mcp).
- Default process collectors (CPU, memory, GC) registered on the same registry.

### Routes (on `create_app`)

- `GET {prometheus_path}` -> `200`, `text/plain; version=0.0.4`, the
  `generate_latest(registry)` exposition. The existing `GET /metrics` JSON route is
  untouched.
- `GET /healthz` -> `200` `{"status": "alive"}` always (liveness; no dependency
  checks).
- `GET /readyz` -> `200` `{"status": "ready"}` only when declared dependencies
  (configured Redis and Postgres URLs, and the exporter queue not saturated) are
  reachable; otherwise `503` `{"status": "not_ready", "failed": [...]}`. Dependency
  checks use the real local services (Docker Compose Postgres/Redis), not mocks.

### Wiring

A small instrumentation seam (`instrument_app(app, registry)`) records HTTP RED
metrics via a FastAPI middleware, and helper functions
(`record_turn`, `record_provider_timeout`, etc.) are called from the card 25
instrumentation points - no business logic duplicated, just metric increments.

## Files to create/modify

- `pyproject.toml` - add `prometheus-client`.
- `src/lucy/serve/prometheus.py` - registry, instruments, helpers, settings.
- `src/lucy/serve/app.py` - mount exposition route + `/healthz` + `/readyz` +
  middleware.
- `tests/test_service_metrics.py` - the tests below.

## Chips

- [ ] **C1 - Registry, settings, and exposition route.** Tests first:
  `test_settings_default_prometheus_path_is_not_metrics` (the path defaults to
  `/metrics/prometheus`, configurable via `LUCY_METRICS_PROMETHEUS_PATH`),
  `test_prometheus_endpoint_serves_exposition` (`TestClient(create_app()).get
  ("/metrics/prometheus")` -> 200, content-type starts `text/plain`, body contains
  `lucy_http_requests_total`), and `test_existing_metrics_json_route_unchanged`
  (`GET /metrics` still returns the business JSON). Implement
  `ServiceMetricsSettings`, the registry/instruments, and the route. Files:
  `pyproject.toml`, `src/lucy/serve/prometheus.py`, `src/lucy/serve/app.py`,
  `tests/test_service_metrics.py`. Verify:
  `.venv/bin/python -m pytest tests/test_service_metrics.py -q -k exposition` ->
  pass.
- [ ] **C2 - RED middleware + work counters.** Tests first:
  `test_http_requests_increment_red_counters` (issue requests, scrape, assert
  `lucy_http_requests_total` and `lucy_http_request_duration_seconds` moved) and
  `test_record_turn_increments_turn_metrics` (call `record_turn(...)` for each
  outcome, assert `lucy_turns_total{outcome=...}` and the duration histogram).
  Implement `instrument_app` middleware and the `record_*` helpers. Files:
  `src/lucy/serve/prometheus.py`, `src/lucy/serve/app.py`,
  `tests/test_service_metrics.py`. Verify:
  `.venv/bin/python -m pytest tests/test_service_metrics.py -q` -> all pass (>=5).
- [ ] **C3 - USE gauges, drop counter, and probes.** Tests first:
  `test_exporter_drop_counter_reflects_observe_queue` (drive card 24's bounded queue
  to drop, scrape, assert `lucy_exporter_dropped_total` matches),
  `test_active_sessions_gauge_tracks_live_sessions`,
  `test_healthz_is_always_alive`, and
  `test_readyz_reports_dependencies` (real local Postgres/Redis up -> 200 ready;
  point a dependency URL at an unused local port -> 503 with that dep in `failed`).
  Implement the USE gauges/counters and the `/healthz` + `/readyz` routes. Files:
  `src/lucy/serve/prometheus.py`, `src/lucy/serve/app.py`,
  `tests/test_service_metrics.py`. Verify:
  `.venv/bin/python -m pytest tests/test_service_metrics.py -q` -> all pass (>=9).
- [ ] **C4 - Smoke test + full suite + bookkeeping.** Smoke (agents.md): start the
  app and
  `curl -s localhost:8000/metrics/prometheus | grep -cE '^lucy_(turn_duration_seconds|active_sessions|exporter_dropped_total)'`
  -> prints `>=3`. Run the whole suite, fill "Improvements noted", move this card to
  `done/`. Verify: `.venv/bin/python -m pytest -q` -> full suite green (use Docker
  Compose `docker compose run --rm lucy-api pytest` when the daemon is available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); `/readyz` checks the real local
  Postgres/Redis from Docker Compose, and the exporter-drop test drives the real
  card 24 queue. Simulate an unreachable dependency with an unused local port, not a
  patched function.
- Do not hardcode the exposition path, metric namespace, ports, or histogram buckets
  at call sites; they live in `ServiceMetricsSettings` or named constants.
- Do not change the existing `GET /metrics` JSON contract; the Prometheus endpoint
  is a separate path.
- Do not add storage, persistence, or any multi-tenant/hosted aggregation; this is
  in-process exposition only (ADR 0010 - hosted observability is closed).
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_service_metrics.py -q` -> all pass
      (>=9 tests), covering exposition, RED, USE, drop counter, and both probes
- [ ] `curl -s localhost:8000/metrics/prometheus | grep -cE '^lucy_(turn_duration_seconds|active_sessions|exporter_dropped_total)'`
      -> prints `3` (RED + USE instruments exposed by the running app)
- [ ] `curl -s -o /dev/null -w '%{http_code}' localhost:8000/metrics` -> prints
      `200` and the body is still the business JSON (existing contract intact)
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, card 24's drop counter is not reachable, or card 25's
instrumentation points are not yet wired: do NOT check boxes, do NOT force tests
green, do NOT fake metric values. Leave the card in `in_progress/`, document the
gap under "Improvements noted" (it may be a card 24/25 follow-up), and report.
Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
