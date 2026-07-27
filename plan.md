# Lucy plan

## S8 Launch (closing)

- [x] **93** — Launch base hygiene
- [x] **50** — Public CI pipeline
- [x] **49** — README + six public guides
- [~] **46** — Pre-publication scrub (C1–C6 done; C7/C8 blocked on TWINE tokens)

## S9 Analytics & SRE observability (in progress)

1. [x] **52** — Analytics model v1 spec
2. [x] **98** — Cost provider attribution on the wire
3. [x] **99** — Measured talk-ratio telemetry (`TalkMeasures` / `aggregate_talk_measures`)
4. [x] **103** — Complete in-process `analytics-model/v1` engine
   (`build_facts`, `session_rollup`, `run_rollup`, percentiles, derived metrics)
5. [ ] **54** — Expose local analytics surface (depends on 103)
6. [ ] **55** — Service observability / Prometheus
7. [ ] **56** — SLOs, alerts, ops dashboards
8. [ ] **57** — Round out telemetry Prometheus exporter

## Current

- Card **103** complete in the public SDK: typed fact grains, deterministic
  event-to-fact builder, full v1 dimensions/measures, nearest-rank percentiles,
  derived metrics with null-on-zero-denominator, and `SessionRollup`/`RunRollup`
  snapshots matching the normative worked example. Card 99 talk measures remain
  compatible. Engine stays ephemeral (ADR 0010 scope cap).
- Card **46** scrub remains parked under `need_human_testing/` pending
  TestPyPI/PyPI tokens for C7/C8.
- Operating docs and backlog live under `lucy-platform/ops/lucy-sdk/`.

## Notes

- Do not edit `docs/analytics-model-v1.md` from consumer cards; open a docs-only
  follow-up if the worked-example `window_start_ms` vs `dimensions.time`
  mismatch should be reconciled.
- Next consumer of the engine: Card **54** local analytics surface.
