# 56 - Add SLOs, alert rules, and self-host ops dashboards

**Sprint:** S9 - Analytics & SRE observability
**Epic:** Observability (SRE)
**Estimated effort:** ~5 h
**Depends on:** 55
**State:** pending

## Goal

Ship the operability layer over card 55's metrics as versioned, self-host config:
typed SLO definitions, Prometheus burn-rate alert rules, a scrape config, and a
Grafana dashboard for the RED/USE instruments, plus the local Prometheus + Grafana
services in Docker Compose. An operator running Lucy on their own infrastructure
gets working SLOs and alerts out of the box; hosted multi-tenant observability stays
the closed platform's job (ADR 0010).

## Context primer

An agent with no prior knowledge must be able to orient from this section.
Read, in this order, before writing anything:

- `agents.md` - operating rules (English, no hardcoded ports/thresholds outside
  typed config or named constants).
- `docs/adr/0013-analytics-and-sre-observability.md` (card 52) - records that
  self-host ops definitions are OPEN and hosted multi-tenant alerting is closed.
- `src/lucy/serve/prometheus.py` (card 55) - the metric names the alerts and the
  dashboard reference (`lucy_http_requests_total`, `lucy_turn_duration_seconds`,
  `lucy_turns_total`, `lucy_active_sessions`, `lucy_exporter_dropped_total`, etc.).
  The SLO/alert/dashboard files MUST reference these exact names.
- `infra/otel-collector-config.yaml` - the existing infra config style and the
  collector service the compose file already runs.
- `docker-compose.yml` - the current local stack (lucy-api, postgres, redis,
  otel-collector); the new Prometheus + Grafana services follow its conventions
  (named volumes, no host-hardcoded secrets, ports from env where the file already
  does so).
- `tests/test_infrastructure.py` - the house pattern for validating compose/infra
  files by parsing them and asserting structure; model the new infra test on it.

## Spec

### SLO definitions - `infra/slo/lucy-slos.yaml`

A typed YAML document (parsed and validated in test) listing the service SLOs, each
with `name`, `objective` (e.g. `0.99`), `window` (e.g. `30d`), and the
`metric`/`good`/`total` PromQL expressions referencing card 55 metric names. At
least three SLOs: turn latency (p95 turn duration under budget), request
availability (non-5xx ratio), and exporter health (drop ratio under budget). The
latency budget value lives here once, not duplicated in the alert file (the alert
references the SLO).

### Alert rules - `infra/prometheus/alerts.yml`

A Prometheus rule group (valid `groups:` schema) with multi-window burn-rate alerts
derived from the SLOs: fast-burn and slow-burn alerts per SLO, each with `alert`,
`expr`, `for`, `labels.severity`, and an `annotations.summary`. Every `expr`
references metric names that exist in card 55.

### Scrape config - `infra/prometheus/prometheus.yml`

A minimal Prometheus config scraping `lucy-api` at the card 55 exposition path,
with `rule_files` including `alerts.yml`. Scrape target host/port come from the
compose service name, not a hardcoded IP.

### Grafana dashboard - `infra/grafana/lucy-runtime.json`

A Grafana dashboard JSON titled `Lucy runtime` with panels for the RED metrics
(request rate, error rate, turn-duration p95) and the USE metrics (active sessions,
queue depths, dropped exports). Each panel's target `expr` references a card 55
metric name. Provisioning files (`infra/grafana/provisioning/...`) wire the
Prometheus datasource and auto-load the dashboard.

### Docker Compose

Extend `docker-compose.yml` with `prometheus` and `grafana` services on the
existing network, mounting the four config files above, with named volumes for
their data and ports exposed via env vars where the file already parameterizes
ports. `docker compose config` MUST stay valid.

### Test - `tests/test_observability_infra.py`

- `test_slo_file_parses_and_lists_required_slos` - YAML parses; the three named
  SLOs are present with objective/window/expr fields.
- `test_alert_rules_are_valid_and_reference_known_metrics` - `alerts.yml` parses as
  a Prometheus rule group; every alert `expr` references a metric name that is also
  defined in `src/lucy/serve/prometheus.py` (read the names from the module, do not
  hardcode the list).
- `test_grafana_dashboard_parses_and_targets_known_metrics` - the dashboard JSON
  parses and every panel target references a known card 55 metric name.
- `test_compose_defines_prometheus_and_grafana` - `docker-compose.yml` parses and
  contains the two services mounting the new config files.

## Files to create/modify

- `infra/slo/lucy-slos.yaml`
- `infra/prometheus/prometheus.yml`, `infra/prometheus/alerts.yml`
- `infra/grafana/lucy-runtime.json`, `infra/grafana/provisioning/...`
- `docker-compose.yml`
- `tests/test_observability_infra.py`

## Chips

- [ ] **C1 - SLO definitions + test.** Test first:
  `test_slo_file_parses_and_lists_required_slos`. Author `infra/slo/lucy-slos.yaml`
  with the three SLOs and their PromQL referencing card 55 metrics. Files:
  `infra/slo/lucy-slos.yaml`, `tests/test_observability_infra.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observability_infra.py -q -k slo` -> pass.
- [ ] **C2 - Alert rules + scrape config, cross-checked against metric names.** Test
  first: `test_alert_rules_are_valid_and_reference_known_metrics` (read defined
  metric names from `src/lucy/serve/prometheus.py`; assert each alert `expr`
  references one). Author `infra/prometheus/alerts.yml` and `prometheus.yml`. Files:
  `infra/prometheus/alerts.yml`, `infra/prometheus/prometheus.yml`,
  `tests/test_observability_infra.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observability_infra.py -q -k alert` -> pass.
- [ ] **C3 - Grafana dashboard + compose services.** Tests first:
  `test_grafana_dashboard_parses_and_targets_known_metrics` and
  `test_compose_defines_prometheus_and_grafana`. Author
  `infra/grafana/lucy-runtime.json`, the provisioning files, and the two compose
  services. Files: `infra/grafana/lucy-runtime.json`,
  `infra/grafana/provisioning/...`, `docker-compose.yml`,
  `tests/test_observability_infra.py`. Verify:
  `.venv/bin/python -m pytest tests/test_observability_infra.py -q` -> all pass
  (>=4).
- [ ] **C4 - Compose smoke + full suite + bookkeeping.** Smoke (agents.md):
  `docker compose config >/dev/null && echo OK` -> prints `OK` with the new
  services present. Run the whole suite, fill "Improvements noted", move this card
  to `done/`. Verify: `.venv/bin/python -m pytest -q` -> full suite green (use
  Docker Compose `docker compose run --rm lucy-api pytest` when the daemon is
  available).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); the tests parse the real
  config files and read the real metric names from `src/lucy/serve/prometheus.py`,
  never a hand-copied list.
- Do not hardcode SLO thresholds, scrape targets, ports, or datasource URLs in more
  than one place; the latency budget lives once in the SLO file and alerts
  reference it, and targets use compose service names.
- Do not add a hosted, multi-tenant, or authenticated observability backend; these
  are self-host definitions only (ADR 0010 - hosted observability is closed).
- Do not duplicate the dashboard or alerts into the closed platform repo from here.
- Do not check a Definition of Done box without running its command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_observability_infra.py -q` -> all pass
      (>=4 tests), covering SLOs, alerts, dashboard, and compose services
- [ ] `docker compose config >/dev/null && echo OK` -> prints `OK` (compose still
      valid with prometheus + grafana added)
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
      `docker compose run --rm lucy-api pytest` when the daemon is available)
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a referenced metric name does not exist in card 55, or
`docker compose config` is invalid: do NOT check boxes, do NOT force tests green.
Leave the card in `in_progress/`, document the gap under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
