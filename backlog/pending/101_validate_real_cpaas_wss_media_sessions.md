# 101 - Validate real CPaaS WSS media sessions end to end

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~12 h
**Depends on:** 42; this is a follow-up to the Card 42 gap, not part of Card 42 implementation
**State:** pending

## Goal

Turn the unresolved real-transport gap after Card 42 into reproducible evidence:
identify why Telnyx reaches the public WSS endpoint but the origin reports EOF,
then prove one real Telnyx session and one real Twilio session deliver inbound
media, carry Lucy playback, and stop cleanly through the deployed TLS and proxy
path. Preserve only fully redacted, credential-safe session fixtures and a
diagnostic record that another agent can rerun without access to secrets.

## Context primer

Read these files in order before changing code, fixtures, or deployment
configuration:

- `backlog/agent_index.md` - backlog states, TDD/no-mocks rules, and evidence
  expectations.
- `backlog/need_human_testing/42_add_cpaas_pstn_adapter.md` - the implementation scope
  and the explicit live-smoke boundary this card follows.
- `docs/cpaas-pstn.md` - WSS authentication, control-channel separation,
  provider hangup behavior, replay limits, and live-smoke commands.
- `tests/fixtures/cpaas/README.md` - the current official-doc transcripts and
  the redaction requirements that a real capture must meet.
- `media-gateway-rust/src/cpaas/runtime.rs` - public media listener, upgrade,
  authentication, and session lifecycle behavior.
- `media-gateway-rust/src/cpaas/call_control.rs` and
  `media-gateway-rust/tests/cpaas_media_stream.rs` - provider control actions,
  playback, stop assertions, and existing local protocol coverage.
- `docs/telephony-connectivity.md` - CPaaS transport assumptions and Spain
  testing constraints.

## Spec

- Diagnose the Telnyx WSS upgrade through one real TLS terminator or reverse
  proxy that presents the configured public `wss://` URL and forwards to the
  gateway. Record the proxy type and version, listener/origin endpoints by
  redacted role only, TLS termination result, upgrade status, forwarding
  result, close code/reason, and timestamps relative to the call. Explain the
  observed origin EOF and identify the smallest corrective change if the
  failure is in Lucy, the proxy, TLS configuration, or provider setup.
- Prove provider-source CIDR admission at the trusted edge. Record the edge
  rule that accepts documented provider ranges and rejects a non-provider
  source before traffic reaches the gateway. Configure
  `LUCY_CPAAAS_ALLOWED_CIDRS` only for the gateway's immediate trusted proxy
  peers, and do not use untrusted forwarded-address headers for admission.
- Use the same credential-safe procedure for a real Telnyx media session and a
  real Twilio media session. Capture the complete WebSocket JSON/text control
  frames and binary media-frame metadata required to replay the session from
  upgrade through terminal stop. Capture no raw secrets, authorization
  headers, signature values, phone numbers, account identifiers, call IDs,
  stream IDs, IP addresses, customer text, or caller/agent audio.
- Store each accepted capture as a deterministic, byte-replayable fixture under
  `tests/fixtures/cpaas/live/`, with a sidecar provenance manifest containing
  provider, capture date, redaction version, codec/sample rate, direction,
  frame ordering, and a hash of the redacted fixture. The manifest must not
  contain provider credentials or identifying values.
- Extend the fixture tests so live fixtures are structurally validated without
  inventing provider behavior: redaction rejects forbidden fields and secret
  patterns; frame ordering includes upgrade/session start, inbound media,
  playback acknowledgement or mark, and terminal stop; media bytes remain
  outside Python and are handled by the Rust media plane.
- Exercise each provider fixture through the real local WebSocket and HTTP
  protocol servers already used by the adapter tests. Assert all of the
  following for both providers: at least one non-empty inbound media frame is
  consumed; a Lucy playback directive produces provider playback bytes or the
  provider's documented playback acknowledgement; a clear/stop action is
  issued; the provider session reaches a terminal state; the gateway emits one
  clean `session.ended` event; and no post-stop media or duplicate hangup is
  observed.
- Add a guarded live smoke/audit command or extend the existing one so missing
  credentials skip with named environment variables, while a configured run
  fails if the public WSS upgrade, inbound media, playback, or clean stop is
  not observed. The command must redact output before writing logs and must not
  make a provider API request merely to test configuration.
- Update the CPaaS documentation to separate the current official-doc
  transcripts from real captures, document the TLS terminator/proxy checks,
  name the exact end-to-end evidence fields, and preserve the rule that a
  provider API dial accepted response is insufficient evidence.
- This card may diagnose or minimally correct the real transport seam required
  for the evidence. It must not redesign the CPaaS adapter or broaden Card 42's
  provider behavior; any implementation defect outside that seam becomes a
  separate follow-up card.

## Files to create/modify

- `tests/fixtures/cpaas/live/` - fully redacted Telnyx and Twilio captures and
  provenance manifests.
- `tests/fixtures/cpaas/README.md` - distinguish real captures from documented
  protocol transcripts and describe the capture/redaction process.
- `media-gateway-rust/tests/cpaas_media_stream.rs` and focused CPaaS tests -
  replay inbound media, playback, terminal stop, and duplicate-stop negatives.
- `media-gateway-rust/src/cpaas/` - only the minimal WSS/proxy or lifecycle fix
  proven by the diagnosis, if required.
- `docs/cpaas-pstn.md` - TLS/proxy diagnosis, live evidence checklist, and
  redacted fixture procedure, including trusted-edge source admission.
- `.env.example` - only documented non-secret variable names or proxy settings,
  if the verified smoke command requires them.

## Chips

- [ ] **C1 - Reproduce and isolate the Telnyx WSS upgrade.** First write a
  failing diagnostic test or executable assertion for the observed origin EOF
  that distinguishes TLS termination, proxy forwarding, WebSocket upgrade,
  authentication, and origin close behavior. Files:
  `media-gateway-rust/tests/cpaas_runtime.rs`,
  `media-gateway-rust/src/cpaas/runtime.rs`, `docs/cpaas-pstn.md`. Verify:
  `docker run --rm -v "$PWD":/repo -w /repo/media-gateway-rust rust:1.82-slim cargo test cpaas_runtime`
  -> the new assertion is red before the diagnosis and green after the minimal
  seam fix or documented confirmed external cause.
- [ ] **C1b - Prove trusted-edge source admission.** Add an executable edge or
  firewall check that accepts a documented provider source range, rejects a
  non-provider source before gateway forwarding, and confirms the gateway sees
  only an allowlisted proxy peer. Record the trusted proxy chain without IP
  addresses or credentials. Files: deployment edge configuration and
  `docs/cpaas-pstn.md`. Verify: the edge conformance command passes both the
  allowed and rejected source cases, while gateway authentication remains
  mandatory.
- [ ] **C2 - Capture and redact real provider sessions.** First write failing
  fixture-contract tests for forbidden secrets/identifiers, provenance fields,
  non-empty inbound media, playback evidence, and terminal stop for both
  providers. Then capture one real Telnyx and one real Twilio session through
  the real TLS terminator/proxy and redact before persistence. Files:
  `tests/fixtures/cpaas/live/`, `tests/fixtures/cpaas/README.md`,
  `media-gateway-rust/tests/cpaas_media_stream.rs`. Verify:
  `docker compose --profile gateway-it run --rm lucy-gateway-tests cargo test cpaas_media_stream`
  -> both live fixtures pass redaction and contain the required ordered events,
  or the card records the exact provider-side blocker without claiming capture.
- [ ] **C3 - Prove replayed inbound, playback, and clean stop.** First add a
  negative test that fails on zero inbound bytes, missing playback/mark,
  duplicate hangup, or media after terminal stop. Replay each redacted real
  capture through real local WebSocket and HTTP servers and assert one terminal
  `session.ended` event per provider. Files:
  `media-gateway-rust/tests/cpaas_media_stream.rs`,
  `media-gateway-rust/src/cpaas/call_control.rs`,
  `media-gateway-rust/tests/cpaas_call_control.rs`. Verify:
  `docker compose --profile gateway-it run --rm lucy-gateway-tests cargo test cpaas`
  -> Telnyx and Twilio replay tests pass, including every negative assertion.
- [ ] **C4 - Document the live audit and close honestly.** First add a failing
  smoke-output test for missing-credential skip and redacted failure output;
  then document the exact proxy, WSS, evidence, and rollback steps and run the
  guarded smoke when credentials are available. Files:
  `docs/cpaas-pstn.md`, `tests/fixtures/cpaas/README.md`, `.env.example`, and
  the existing live-smoke test/command. Verify:
  `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> configured runs
  report upgrade, inbound media, playback, and clean stop, while unconfigured
  runs exit 0 with only missing variable names.
- [ ] **C5 - Run gates and record evidence.** Run the focused Rust and Docker
  tests, Python suite, lint, format, and mypy; fill Improvements noted and
  Review evidence, then move this card to `done/` only when both real captures
  and both end-to-end proofs exist, otherwise move it to
  `need_human_testing/` with the exact remaining provider action. Files: this
  card and all files listed above. Verify:
  `docker compose run --rm lucy-api pytest tests/test_backlog_contract.py -q`
  -> contract passes, and `docker compose run --rm lucy-api pytest -q` -> full
  Python suite passes.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003); use the real TLS
  terminator/proxy, real provider sessions, deterministic local protocol
  servers, and recorded real captures.
- Do not hardcode provider names, model names, URLs, secrets, phone numbers,
  thresholds, ports, regions, currencies, quotas, or latency budgets outside
  typed settings, registries, named constants, or dated documentation.
- Do not commit raw provider frames, authorization headers, signatures, tokens,
  phone numbers, account/call/stream identifiers, IP addresses, transcripts,
  or audio; redact before persistence and review the diff manually.
- Do not treat an accepted Telnyx or Twilio dial, a reachable WSS URL, or a
  proxy HTTP 101 response as proof of media, playback, or clean stop.
- Do not put audio frames or provider payloads into Python or the control
  channel, redesign Card 42, weaken authentication, or close the card with
  official-doc transcripts presented as real captures.
- Do not claim a live result when credentials, provider permissions, TLS
  termination, proxy forwarding, or a human call action is unavailable.

## Definition of Done

- [ ] `docker compose --profile gateway-it run --rm lucy-gateway-tests cargo test cpaas` -> all CPaaS tests pass, including both redacted real-capture replays and stop negatives.
- [ ] `docker compose --profile cpaas-smoke run --rm cpaas-smoke` -> each configured provider records WSS upgrade, non-empty inbound media, playback evidence, and one clean terminal stop; an unconfigured run skips with exit 0 and only missing variable names.
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0.
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0.
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0.
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green.
- [ ] `git diff --check` -> no whitespace errors, and the diff contains no secret, identifier, or raw-audio material.
- [ ] The card records the proxy diagnosis, trusted-edge CIDR evidence, exact Telnyx and Twilio capture provenance, redaction review, and follow-up cards for unresolved issues before moving state.

## Failure protocol

If either provider cannot produce a real capture, the TLS terminator/proxy
cannot be safely exposed, or any redaction check fails, stop at the last
honest boundary. Do not synthesize a live fixture from documentation, weaken
authentication, or mark playback and clean stop as proven from dial
acceptance. Leave the card pending or move it to `need_human_testing/`, record
the exact command, timestamp, provider-side response, proxy/origin evidence,
and missing human action in Improvements noted, and raise a focused follow-up
for any implementation defect outside the minimal seam.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

- code-reviewer: PENDING
- test-auditor: PENDING
- docs-reviewer: PENDING
- simplicity-reviewer: PENDING
- security-reviewer: PENDING

Findings disposition:

- Pending.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
