# 40 - Build the local Asterisk telephony lab

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~10 h
**Depends on:** 32
**State:** done

## Goal

Create the no-mocks telephony integration substrate for Sprint S5: a real
Asterisk 22 Docker lab, deterministic scripted callers, and softphone guidance
that can drive Lucy through the same control-channel boundary used by the Rust
gateway and provider adapters.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - Docker Compose is the sanctioned runtime; no mocks; no
  hardcoded ports, URLs, providers, or thresholds outside typed settings or
  named constants.
- `docs/adr/0003-no-mocks-testing-policy.md` - real local protocol servers and
  deterministic simulators are allowed; mocking frameworks are not.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - the media plane
  owns sockets and audio; Python receives only control events.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the control-channel
  schema the lab must eventually feed.
- `docs/adr/0012-telephony-native-first.md` - S5 delivery order and the local
  lab's role as the P0 integration substrate.
- `docs/telephony-connectivity.md` - verified protocol facts and the exact
  local no-mocks lab recommendation.
- `docker-compose.yml` - add the lab behind an opt-in profile so default
  runtime gates remain lean.
- `media-gateway-rust/` - the sidecar that will receive telephony audio once
  card 51 lands.
- `tests/test_infrastructure.py` - house infrastructure contract style.

## Spec

- Add an opt-in Docker Compose profile named `telephony-lab` with a pinned
  Asterisk 22 image, a deterministic caller service, and all config mounted
  read-only from `infra/asterisk/`.
- Asterisk config must expose a local-only PJSIP endpoint for manual
  softphones and a scripted test extension that answers and forks media through
  AudioSocket to the gateway hostname/port from environment variables.
- The lab must use real protocol components: Asterisk, ARI HTTP, SIPp or an
  approved Docker caller, and real WAV fixtures. Do not simulate SIP or RTP in
  Python.
- Add `.env.example` entries for lab-only settings using `LUCY_TELEPHONY_*`
  names. Defaults belong in typed settings, shell env, or named constants, not
  inline code.
- Add deterministic health checks: Asterisk process health, ARI reachability,
  and a scripted originate or SIPp scenario that produces an observable call
  result.
- Add `docs/local-telephony-lab.md` with softphone registration, scripted
  caller commands, troubleshooting, and the boundary reminder that audio never
  crosses into Python.

## Files to create/modify

- `docker-compose.yml` - opt-in `telephony-lab` services and profiles.
- `.env.example` - lab settings with safe local defaults only.
- `infra/asterisk/` - Asterisk config, fixtures, and caller scenarios.
- `docs/local-telephony-lab.md` - manual and scripted lab guide.
- `tests/test_infrastructure.py` - contract tests for lab files and profile.
- This card file.

## Chips

- [x] **C1 - Lab file contract.** Write failing infrastructure tests first:
  `test_telephony_lab_profile_exists` and
  `test_asterisk_lab_files_are_committed`. Then add the compose profile and
  minimal `infra/asterisk/` tree. Files: `docker-compose.yml`,
  `infra/asterisk/`, `tests/test_infrastructure.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> all pass.
- [x] **C2 - Asterisk boots with local config.** Add Asterisk config and a
  container health check that proves ARI and PJSIP load. Files:
  `infra/asterisk/`, `.env.example`, `docker-compose.yml`. Verify:
  `docker compose --profile telephony-lab up --build -d asterisk` followed by
  `docker compose --profile telephony-lab ps asterisk` -> service healthy.
- [x] **C3 - Scripted caller proves the lab.** Add the deterministic caller
  scenario and a documented command that originates a call to the test
  extension. Files: `infra/asterisk/`, `docs/local-telephony-lab.md`. Verify:
  `docker compose --profile telephony-lab run --rm telephony-caller` -> call
  completes with an observable Asterisk channel result.
- [x] **C4 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
  type checks, record evidence, and move this card to `done/` or
  `need_human_testing/` if manual softphone verification is still required.
  Verify: `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; use real Asterisk and real local
  protocol clients.
- Do not hardcode provider names, hosts, ports, credentials, or retry budgets
  outside env settings, typed settings, registries, or named constants.
- Do not expose the lab on public interfaces by default.
- Do not make the default `docker compose up --build` start telephony services.
- Do not send audio frames into Python.

## Definition of Done

- [x] `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> 9 passed; the profile, files, WAV format, environment defaults, localhost
  ports, and media-plane boundary are covered.
- [x] `docker compose --profile telephony-lab up --build -d asterisk` ->
  Asterisk 22.10.1 became healthy with ARI and `chan_pjsip` running.
- [x] `docker compose --profile telephony-lab run --rm telephony-caller` ->
  the deterministic ARI call completed after successful WAV playback.
- [x] `docker compose run --rm lucy-api ruff check src tests` -> exit 0;
  the same gate also passed for `infra/asterisk/scripts`.
- [x] `docker compose run --rm lucy-api ruff format --check src tests` ->
  140 files already formatted, including the lab scripts.
- [x] `docker compose run --rm lucy-api mypy src` -> no issues in 71 files.
- [x] `docker compose run --rm lucy-api pytest -q` -> 1,289 passed, 2
  deselected.
- [x] Post-task audit done; AudioSocket end-to-end verification remains in
  existing dependent card 41.

## Failure protocol

If Asterisk cannot boot, the caller cannot complete, or Docker pulls an
unacceptable unpinned image: do NOT check boxes, do NOT move to `done/`.
Record the exact command and output under "Improvements noted".

## Improvements noted

- The Asterisk image is pinned by digest; direct inspection reports Asterisk
  22.10.1.
- Source configuration stays read-only. A small renderer writes validated
  environment substitutions to an in-memory `/etc/asterisk` before the image
  drops to the `asterisk` user.
- The module list loads only the ARI, PJSIP, AudioSocket, Local-channel, WAV,
  and codec dependencies required by the lab instead of the image's full
  module catalog.
- The caller reports success only after `PLAYBACKSTATUS=SUCCESS`; ARI HTTP,
  connection, JSON, call-deadline, and response-timeout failures exit with the
  failing operation named.
- Host and container port mappings consume the same named settings, all host
  bindings are localhost-only, and the AudioSocket target remains private to
  the Compose network.
- Third-party telephony containers receive explicit per-service environment
  allowlists, so provider, database, Redis, and telemetry secrets from the
  project `.env` never enter the lab.
- The renderer rejects control characters, config metacharacters, invalid
  identifiers, out-of-range ports, reversed RTP ranges, and non-finite
  timeouts without echoing values. The caller likewise bounds every deadline
  before opening a socket.
- Asterisk health now requires the named `lucy-lab` PJSIP endpoint in addition
  to the process, module, and authenticated ARI checks.
- Real local HTTP protocol tests cover ARI success, invalid credentials, a
  call that never completes, a stalled response, connection failures, invalid
  JSON, non-object payloads, and peer resets without mocking. The focused lab
  suite passes 35 tests.
- Renderer and caller validation share one small settings module, and rendered
  output is checked after validation to prevent the two paths from drifting.
- TDD red evidence: the first infrastructure run failed 2 tests for the
  absent profile/files; the first Asterisk boot failed on unescaped dialplan
  variables; the first caller runs exposed missing codec/global modules and a
  premature observer hangup; caller error tests initially failed 2 cases; and
  the response-timeout regression initially leaked `TimeoutError`.
- No new follow-up card is required. Existing card 41 owns the real Rust
  AudioSocket listener and end-to-end media-fork assertion; the optional
  softphone path is documented but is not needed to close this automated lab
  substrate.

## Review evidence

- code-reviewer: PASS (`019f673e-4e01-7a50-a141-d0a4a11cf685`); 35 focused
  tests plus 1,289 full tests are green on the final current diff.
- test-auditor: PASS (`019f673e-52cd-7003-9da9-39f3a7c27b59`); 35 focused
  tests, the real Docker caller, and no-mocks gates pass.
- docs-reviewer: PASS via Luna fallback
  (`019f6740-00da-7201-be30-4110ce6f2f05`); the configured
  `gpt-5.4-nano` model is unavailable for this account. Commands, settings,
  ADRs, and evidence are consistent.
- simplicity-reviewer: PASS (`019f673e-59ab-7272-a19c-dd8594ec4fe0`);
  the final diff keeps validation shared and rendered output tested.
- security-reviewer: PASS (`019f673f-fce2-7260-b5e3-b159bb242767`);
  environment allowlists, localhost bindings, image pin, settings validation,
  fixture provenance, secret scans, timeout bounds, and the media-plane
  boundary pass on the final current diff.
- final-integrator: PASS (`019f6742-a35c-70d2-9b7e-97d9b543dab6`); all
  specialist evidence, real-service gates, scope exclusions, no-mocks policy,
  and the media-plane boundary are complete with no unresolved findings.

Findings disposition:

- Fixed: Compose host and container ports use one setting; the caller no
  longer assigns a random ARI channel id.
- Fixed: every ARI request has a bounded timeout and translates HTTP,
  connection, response-timeout, peer-reset, JSON, and payload errors without
  leaking credentials or tracebacks.
- Fixed: real local protocol tests cover caller success and all named failure
  branches with manual pacing for the call deadline.
- Fixed: Asterisk health requires process, authenticated ARI, running PJSIP,
  and the configured `lucy-lab` endpoint.
- Fixed: third-party containers receive only explicit telephony environment
  allowlists; shared validation rejects config injection, invalid endpoints,
  unsafe credentials, invalid ranges, and non-finite deadlines.
- Fixed: documentation now includes `slin16`, Compose readiness waiting, and
  service-scoped teardown.
- Fixed: C4 was checked only after final-integrator PASS immediately before
  this canonical move and focused commit.
- Fixed: renderer and caller now share one strictly-positive call-timeout
  contract and one maximum constant; both renderer and caller reject zero,
  while deadline tests use manual pacing.
- Rejected as out of scope: executing AudioSocket against Rust belongs to
  dependent card 41, which owns the listener and end-to-end assertion. Card 40
  provides the environment-driven dialplan route and proves its own explicit
  Asterisk/ARI/WAV exit gate.
- Excluded from scope: pre-existing `agents.md`, `CLAUDE.md`, and `.codex/`
  changes are unrelated and will not be staged in the Card 40 commit.

## Pending human testing

None. The optional softphone path is documented; the deterministic real
Asterisk caller satisfies this card's automated acceptance gate.
