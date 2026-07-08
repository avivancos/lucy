# 40 - Build the local Asterisk telephony lab

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~10 h
**Depends on:** 32
**State:** pending

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

- [ ] **C1 - Lab file contract.** Write failing infrastructure tests first:
  `test_telephony_lab_profile_exists` and
  `test_asterisk_lab_files_are_committed`. Then add the compose profile and
  minimal `infra/asterisk/` tree. Files: `docker-compose.yml`,
  `infra/asterisk/`, `tests/test_infrastructure.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> all pass.
- [ ] **C2 - Asterisk boots with local config.** Add Asterisk config and a
  container health check that proves ARI and PJSIP load. Files:
  `infra/asterisk/`, `.env.example`, `docker-compose.yml`. Verify:
  `docker compose --profile telephony-lab up --build -d asterisk` followed by
  `docker compose --profile telephony-lab ps asterisk` -> service healthy.
- [ ] **C3 - Scripted caller proves the lab.** Add the deterministic caller
  scenario and a documented command that originates a call to the test
  extension. Files: `infra/asterisk/`, `docs/local-telephony-lab.md`. Verify:
  `docker compose --profile telephony-lab run --rm telephony-caller` -> call
  completes with an observable Asterisk channel result.
- [ ] **C4 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
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

- [ ] `docker compose run --rm lucy-api pytest tests/test_infrastructure.py -q`
  -> telephony lab contract tests pass
- [ ] `docker compose --profile telephony-lab up --build -d asterisk` ->
  Asterisk service becomes healthy
- [ ] `docker compose --profile telephony-lab run --rm telephony-caller` ->
  deterministic call completes
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If Asterisk cannot boot, the caller cannot complete, or Docker pulls an
unacceptable unpinned image: do NOT check boxes, do NOT move to `done/`.
Record the exact command and output under "Improvements noted".

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
