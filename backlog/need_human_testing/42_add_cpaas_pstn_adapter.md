# 42 - Add the CPaaS PSTN adapter

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~14 h
**Depends on:** 51
**State:** need_human_testing

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
- `media-gateway-rust/Dockerfile` and `docker-compose.yml` - typed TCP listener
  exposure, runtime selection, and smoke wiring.
- `src/lucy/specs.py` - CPaaS transport registry entries.
- `src/lucy/settings.py` - typed CPaaS settings.
- `src/lucy/session.py`, `src/lucy/harness.py`, and
  `src/lucy/serve/control_ws.py` - session lifecycle cost emission.
- `src/lucy/observe/events.py`, `src/lucy/jurisdiction.py`, and
  `src/lucy/transport/cpaas_smoke.py` - cost metadata, ISO jurisdiction
  validation, and guarded smoke implementation.
- `tests/fixtures/cpaas/` - recorded fixture frames with redacted metadata.
- `tests/test_specs.py`, `tests/test_settings.py`, `tests/test_observability.py`
  plus CPaaS pricing, control bridge, infrastructure, and smoke tests - Python
  contract coverage.
- `.env.example`, `docs/cpaas-pstn.md`, `docs/telephony-connectivity.md`, and
  `docs/telemetry-wire-v1.md` - configuration, live-smoke boundary, Spain
  compliance, and telemetry wire documentation.
- `backlog/sprints.md` and follow-up Cards 101/102 - dependency and review
  bookkeeping discovered while executing this card.
- This card file.

## Chips

- [x] **C1 - Registry and typed settings.** Write failing Python tests first
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
- [x] **C3 - Media-stream adapters.** Write Rust tests against a local
  WebSocket protocol server that replays fixture frames. Then implement frame
  decode, playback, cancel, hangup, and metric emission. Files:
  `media-gateway-rust/src/`, `media-gateway-rust/tests/`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> all pass.
- [x] **C4 - Optional live PSTN smoke.** Add a documented command that places
  or receives one live call when credentials are present. Files:
  `docs/cpaas-pstn.md`, `.env.example`. Verify:
  `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> live call
  succeeds, or exits skipped with missing credential names.
- [x] **C5 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
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

- [x] `docker compose run --rm lucy-api pytest tests/test_specs.py tests/test_settings.py -q`
  -> CPaaS settings and registry tests pass
- [x] `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app rust:1.82-slim cargo test`
  -> CPaaS adapter tests pass
- [x] `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> live
  smoke succeeds or card is parked in `need_human_testing/`
- [x] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [x] `docker compose run --rm lucy-api mypy src` -> exit 0
- [x] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If provider docs drift, credentials are unavailable, or fixture frames expose
unredacted PII: stop, leave the card outside `done/`, record the exact issue,
and raise a security follow-up before committing fixtures.

## Improvements noted

- Real Telnyx origination was accepted twice, but the temporary public WSS
  tunnel reached the origin with EOF before the first authenticated media
  frame. Card 101 owns stable TLS/proxy diagnosis, trusted-edge CIDR evidence,
  redacted real Telnyx/Twilio captures, playback, and clean-stop proof.
- The committed fixtures are deterministic, redacted transcripts derived from
  official provider references; they are not presented as live captures.
- Card 102 owns two non-blocking simplicity cleanups: remove the single-state
  telemetry metadata field and consolidate duplicated gateway health selects.

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

- code-reviewer: PASS after corrections to country-code validation and runtime
  behavior.
- test-auditor: PASS after adding provider failure, oversized response,
  timeout, wrong-session, sequence, and cancellation negatives.
- simplicity-reviewer: PASS; two P2 cleanup items deferred to Card 102.
- security-reviewer: PASS after removing unbounded completed-chunk state,
  exercising 10,000 sequential media frames, and assigning provider-source
  CIDR admission to the trusted edge in Card 101.
- docs-reviewer: PASS after correcting the CPaaS container port to TCP and
  clearly separating deterministic Card 42 evidence from Card 101 live proof.
- final-integrator: PASS after the port-protocol gate, C2 bookkeeping,
  findings inventory, and declared file scope were corrected.
- Python gates: `1372 passed, 2 deselected`; ruff check, format check, and mypy
  all pass in Docker.
- Rust gates: fmt, clippy with warnings denied, and the complete test suite pass
  in Docker, including 7 media-stream, 6 call-control, and 6 runtime tests.
- Runtime gates: clean Compose build booted API and gateway healthy; Compose
  config, backlog contract (9 tests), and `git diff --check` pass.
- Live boundary: the guarded smoke skips cleanly without credentials. Two real
  Telnyx dial requests were accepted, but public WSS media was not observed.

Findings disposition:

- `[P1 code-001]` incomplete jurisdiction validation: fixed with the complete
  assigned ISO 3166-1 alpha-2 registry and negative country-code tests.
- `[P1 code-002]` CPaaS runtime lifecycle gaps: fixed with authenticated local
  Telnyx/Twilio servers, bounded handshakes/idleness, sequence/session checks,
  cancellation, hangup, and terminal-event coverage.
- `[P1 test-001]` missing call-control and runtime negatives: fixed with 503
  redaction, oversized success, deterministic timeout, invalid schema,
  wrong-session, and non-increasing-sequence tests.
- `[P1 sec-001]` unbounded completed-media chunk state: fixed by removing the
  redundant collection; a 10,000-frame regression preserves duplicate
  rejection without accumulating completed chunk IDs.
- `[P2 sec-002]` provider CIDRs cannot be inferred behind a reverse proxy:
  trusted-edge enforcement and executable evidence are required by Card 101;
  the gateway validates only its immediate trusted proxy peer and still
  requires provider authentication.
- `[P2 simp-001]` single-state cost metadata field and `[P2 simp-002]`
  duplicated health selection: accepted non-blocking debt in Card 102.
- `[P2 docs-001]` incorrect container port protocol: fixed with explicit TCP
  exposure for the CPaaS/WebSocket ports and both TCP/UDP exposure for the
  mode-dependent 9094 listener; infrastructure tests lock the declaration.
- `[P2 docs-002]` local evidence could be mistaken for public proof: fixed by
  naming the Card 42 deterministic boundary and Card 101 live boundary.
- Reviewer references: security `019f6aad-551f-7721-b057-8239df97418c`,
  simplicity `019f6aad-58ee-7703-b2a4-2253d64f143a`, docs
  `019f6ab7-36d7-7b73-bcd6-e72b83f514b8`, final integration
  `019f6abf-f084-7843-94f3-eeeb595c6e94`; code and test reports are retained
  in this task's review transcript and fully itemized above.

## Pending human testing

- Provision a stable TLS terminator or provider-reachable public WSS endpoint.
- Diagnose the observed proxy/origin EOF and prove provider-source CIDR
  filtering at the trusted edge.
- Complete authenticated Telnyx and Twilio sessions with inbound media, Lucy
  playback, and exactly one clean `session.ended` event.
- Capture fully redacted real session fixtures under Card 101 without phone
  numbers, credentials, identifiers, transcripts, or audio.
