# 42 - Add the CPaaS PSTN adapter

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~14 h
**Depends on:** 51
**State:** pending

## Goal

Give Lucy a day-one real PSTN path through CPaaS media streams while keeping
the same media-plane/control-channel boundary used by native PBX adapters.
Telnyx is the recommended first provider and Twilio is the fast demo fallback,
both resolved through typed provider registries rather than inline strings.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - no mocks, no hardcoded providers or URLs outside registries,
  Docker Compose for tests, and no secrets in git.
- `docs/adr/0003-no-mocks-testing-policy.md` - recorded real interactions and
  local protocol servers are allowed; mocking frameworks are not.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - CPaaS media
  streams stay in the media plane; Python receives control events.
- `docs/adr/0010-open-core-split.md` - SDK adapter code is open; platform
  aggregation and cross-tenant storage stay out.
- `docs/adr/0012-telephony-native-first.md` - CPaaS is the P1 path for real
  PSTN reach.
- `docs/telephony-connectivity.md` - provider choice, Spanish KYC, CLI rules,
  and current cost anchors.
- `backlog/done/51_rust_gateway_implements_control_schema.md` - Rust gateway
  schema conformance and control fixtures.
- `src/lucy/settings.py`, `src/lucy/specs.py`, and `src/lucy/metrics.py` -
  typed settings, transport resolution, and `telephony_cost`.

## Spec

- Add CPaaS transport resolution for Telnyx and Twilio through a registry. Code
  may refer to provider names only through registry constants and typed setting
  values.
- Implement media-stream adapters in the Rust gateway or media-plane package:
  inbound media frames are decoded in Rust, provider control frames map into
  the control-channel schema, and downstream Lucy directives map to provider
  playback/cancel/hangup operations.
- Add local no-secrets test coverage using recorded fixture transcripts from
  real provider sessions plus a local WebSocket protocol server that replays
  those fixtures byte-for-byte.
- Add optional live smoke commands guarded by env vars:
  `LUCY_CPAAAS_PROVIDER`, `LUCY_CPAAAS_ACCOUNT_ID`,
  `LUCY_CPAAAS_API_KEY`, `LUCY_CPAAAS_FROM_NUMBER`,
  `LUCY_CPAAAS_TO_NUMBER`. Missing credentials must skip live smoke with a
  clear message, not fail unit tests.
- Cost and jurisdiction metadata must emit through the existing telemetry seam:
  call direction, provider registry key, country code, billable seconds, and
  cost component. Do not log phone numbers without redaction.
- Document Spain-specific testing constraints: +34 KYC, geographic address,
  registered CLI, and trial limitations.

## Files to create/modify

- `media-gateway-rust/` - CPaaS media-stream adapters and tests.
- `src/lucy/specs.py` - CPaaS transport registry entries.
- `src/lucy/settings.py` - typed CPaaS settings.
- `src/lucy/observe/events.py` - telephony cost metadata only if missing.
- `tests/fixtures/cpaas/` - recorded fixture frames with redacted metadata.
- `tests/test_specs.py`, `tests/test_settings.py`, `tests/test_observability.py`
  - Python contract coverage.
- `docs/cpaas-pstn.md` - live smoke and Spain compliance guide.
- This card file.

## Chips

- [ ] **C1 - Registry and typed settings.** Write failing Python tests first
  for CPaaS transport resolution, env parsing, missing credentials, and no
  inline provider defaults. Then implement settings and registry entries.
  Files: `src/lucy/specs.py`, `src/lucy/settings.py`,
  `tests/test_specs.py`, `tests/test_settings.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> all pass.
- [ ] **C2 - Recorded fixture contract.** Add redacted fixture files from real
  Telnyx and Twilio media-stream handshakes, plus tests proving required frame
  types and redaction. Files: `tests/fixtures/cpaas/`,
  `tests/test_observability.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_observability.py -q`
  -> all pass.
- [ ] **C3 - Media-stream adapters.** Write Rust tests against a local
  WebSocket protocol server that replays fixture frames. Then implement frame
  decode, playback, cancel, hangup, and metric emission. Files:
  `media-gateway-rust/src/`, `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [ ] **C4 - Optional live PSTN smoke.** Add a documented command that places
  or receives one live call when credentials are present. Files:
  `docs/cpaas-pstn.md`, `.env.example`. Verify:
  `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> live call
  succeeds, or exits skipped with missing credential names.
- [ ] **C5 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
  mypy, Rust tests, record evidence, and move this card. If no live PSTN call
  can be executed, move to `need_human_testing/` with exact remaining steps.
  Verify: `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use recorded real fixtures and local
  protocol servers.
- Do not hardcode provider names, URLs, phone numbers, credentials, quotas,
  regions, currencies, prices, or latency budgets outside typed settings,
  registries, named constants, or docs with verification dates.
- Do not commit secrets, account IDs, unredacted phone numbers, or provider
  dashboard URLs.
- Do not let CPaaS-specific behavior leak into the generic control schema.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> CPaaS settings and registry tests pass
- [ ] `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> CPaaS adapter tests pass
- [ ] `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> live
  smoke succeeds or card is parked in `need_human_testing/`
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If provider docs drift, credentials are unavailable, or fixture frames expose
unredacted PII: stop, leave the card outside `done/`, record the exact issue,
and raise a security follow-up before committing fixtures.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
