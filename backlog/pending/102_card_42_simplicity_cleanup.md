# 102 - Close Card 42 simplicity cleanup findings

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~3 h
**Depends on:** 42; this is a follow-up to the two deferred simplicity findings, not a reopening of Card 42's transport scope
**State:** pending

## Goal

Remove two pieces of duplication left by the Card 42 review while preserving
the existing telemetry wire output and gateway startup behavior: make the
telephony cost-component tag a direct constant, and share the gateway health
server's common Tokio selection logic after the transport has been selected.

## Context primer

Read these files in order before changing anything:

- `agents.md` - Docker-only verification, TDD, no-mocks, typed configuration,
  and the media/control plane boundary.
- `backlog/need_human_testing/42_add_cpaas_pstn_adapter.md` - Card 42's frozen
  transport and telemetry scope; this card only closes its deferred cleanup.
- `src/lucy/observe/events.py` - current `CpaasCostMetadata` model and wire-tag
  serialization.
- `tests/test_observability.py` and `tests/test_pricing.py` - existing
  assertions for the telephony cost-component tag.
- `media-gateway-rust/src/main.rs` - gateway startup, transport selection, and
  the duplicated health-server `tokio::select!` branches.
- `media-gateway-rust/Cargo.toml` and `media-gateway-rust/tests/` - sanctioned
  Rust test/runtime setup and existing gateway coverage.

## Spec

- Remove `CpaasCostMetadata.cost_component` from the Pydantic model. Callers
  must no longer be able to provide or override that field through model
  construction or serialization.
- Preserve the exact existing wire tag: every valid CPaaS cost event emits
  `"telephony.cost_component": "telephony_cost"`.
- Emit that value directly from the metadata serializer as a named constant or
  equivalent module-level typed constant; do not replace it with an inline
  provider-specific branch or a caller-supplied value.
- Preserve all existing CPaaS validation for direction, provider, country
  code, and finite non-negative billable seconds, including rejection of
  unknown or lowercase country codes.
- In `media-gateway-rust/src/main.rs`, retain the existing health listener,
  `/health` route, shared metrics state, error text, and transport-selection
  behavior for both CPaaS and Asterisk.
- After the CPaaS-versus-Asterisk configuration branch selects and constructs
  the transport future, run one shared health-server `tokio::select!` path.
  The health server must race the selected transport exactly as it does now,
  and the first completed branch must determine the returned result.
- Keep transport-specific configuration parsing and error conversion in the
  selected transport path. Do not introduce a generic transport trait or move
  audio, provider credentials, or provider payloads across the media/control
  plane boundary.
- Add negative regression coverage proving the removed Python field cannot be
  supplied as a supported metadata field, plus positive coverage proving the
  serialized tag remains unchanged. Add Rust coverage or a source-level
  structural test that both transport modes use the shared health-server
  selection helper/path rather than duplicate `tokio::select!` blocks.

## Files to create/modify

- `src/lucy/observe/events.py` - remove the single-state model field and emit
  the fixed tag value directly.
- `tests/test_observability.py` - test the removed-field rejection and exact
  wire output.
- `tests/test_pricing.py` - preserve the end-to-end CPaaS cost-event tag
  contract.
- `media-gateway-rust/src/main.rs` - consolidate health-server selection after
  transport selection without changing runtime behavior.
- `media-gateway-rust/tests/` - add focused structural or behavioral coverage
  for the shared startup selection path.
- This card file and `backlog/sprints.md` - card bookkeeping only.

## Chips

- [ ] **C1 - Make the CPaaS cost-component tag serializer-owned.** Write the
  failing tests first, then remove `CpaasCostMetadata.cost_component` and emit
  the fixed `telephony_cost` value from `src/lucy/observe/events.py`; preserve
  all other validation and tag formatting. Files:
  `src/lucy/observe/events.py`, `tests/test_observability.py`. Test first:
  `test_cpaas_cost_metadata_rejects_cost_component_override` and the exact
  wire-tag assertion. Verify:
  `docker compose run --rm lucy-api pytest tests/test_observability.py -q`
  -> all observability tests pass, including the new rejection and unchanged
  `telephony.cost_component` output.
- [ ] **C2 - Preserve the pricing integration contract.** Write a failing
  end-to-end regression test first, then update only the affected construction
  or expectation needed after C1 so CPaaS cost events still expose the same
  fixed component tag through the pricing path. Files:
  `tests/test_pricing.py`, `src/lucy/session.py` if the existing construction
  requires adjustment. Test first:
  `test_cpaas_cost_event_contains_fixed_telephony_component`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_pricing.py -q`
  -> all pricing tests pass and the CPaaS event contains exactly
  `telephony.cost_component=telephony_cost`.
- [ ] **C3 - Share gateway health-server selection.** Write a failing Rust
  structural or deterministic startup test first, then select the CPaaS or
  Asterisk transport before entering one shared health-server race. Preserve
  listener ownership, metrics state, result precedence, and existing error
  messages; do not duplicate the `tokio::select!` health branch. Files:
  `media-gateway-rust/src/main.rs`, `media-gateway-rust/tests/`. Test first:
  `gateway_startup_uses_one_shared_health_server_selection_path`. Verify:
  `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test`
  -> all Rust tests pass and the new coverage proves both transport choices
  retain the shared health-server behavior.
- [ ] **C4 - Run focused/full gates and record the cleanup.** Run the focused
  Python and Rust tests, then the complete sanctioned backlog contract and
  project gates; fill this card's Improvements noted and move it through the
  normal state flow only after evidence is recorded. Files: this card only for
  bookkeeping. Test first: no new test; use the regressions from C1-C3.
  Verify: `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> the backlog contract passes with Card 102 mapped to exactly one sprint.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); use real local
  implementations, deterministic simulators, local protocol servers, or
  recorded fixtures.
- Do not hardcode provider names, model names, URLs, thresholds, regions,
  currencies, quotas, or latency budgets outside typed settings, registries,
  or named constants.
- Do not change the CPaaS telemetry wire shape except removing the
  caller-overridable model field while preserving its emitted constant tag.
- Do not change provider resolution, transport protocols, health responses,
  listener addresses, metrics ownership, error messages, or control-channel
  schemas.
- Do not introduce a generic transport abstraction, move audio into Python,
  or touch files outside this card's file list.
- Do not mark a chip or Definition of Done item complete without running its
  stated verification command.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_observability.py tests/test_pricing.py -q` -> all focused Python tests pass, including the removed-field negative test and unchanged fixed tag.
- [ ] `docker run --rm -e CARGO_TARGET_DIR=/tmp/lucy-cargo-target -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test` -> all Rust tests pass, including shared health-server selection coverage.
- [ ] `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q` -> backlog contract passes and Card 102 is mapped exactly once in `backlog/sprints.md`.
- [ ] `docker compose run --rm lucy-api pytest -q` -> full Python suite is green.
- [ ] Post-task audit done; any newly noticed issue is recorded here and raised as a separate follow-up card.

## Failure protocol

If the field removal changes a public wire contract, if a CPaaS or Asterisk
startup regression appears, or if the shared selection path requires a broader
transport abstraction, stop and leave the card outside `done/`. Record the
failing command, exact error, and affected behavior under Improvements noted.
Do not weaken regression tests, restore the duplicate branches just to pass a
structural check, or claim completion without the Docker evidence.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61. -->

- code-reviewer: PENDING
- test-auditor: PENDING
- docs-reviewer: PENDING
- simplicity-reviewer: PENDING
- security-reviewer: PENDING

Findings disposition:

- Pending.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
