# 44 - Write the managed SIP edge blueprint

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~10 h
**Depends on:** 40, 41, 42, 43
**State:** pending

## Goal

Turn the S5 adapter work into an operational blueprint for a managed SIP edge:
an open self-hostable Asterisk 22 LTS design, customer PBX routing recipes, and
a clean line between open SDK code and the closed platform service.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - documentation in English, no secrets, typed settings, and
  Docker Compose gates.
- `docs/adr/0010-open-core-split.md` - open SDK versus closed platform
  boundary.
- `docs/adr/0012-telephony-native-first.md` - managed SIP edge strategy and
  delivery tier.
- `docs/telephony-connectivity.md` - managed edge recommendation, customer PBX
  recipes, Spain KYC and CLI constraints.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - media/control
  boundary the edge must preserve.
- `backlog/done/40_build_local_asterisk_telephony_lab.md` - local lab base.
- `backlog/done/41_add_asterisk_audio_fork_adapter.md` - Asterisk adapter
  behavior and supported modes.
- `backlog/done/42_add_cpaas_pstn_adapter.md` - CPaaS fallback and live PSTN
  smoke notes.
- `backlog/done/43_add_freeswitch_audio_stream_adapter.md` - FreeSWITCH
  adapter and non-managed-edge decision.

## Spec

- Add `docs/managed-sip-edge.md` with the operational blueprint for an
  Asterisk 22 LTS edge: topology, call flow, supported customer PBX patterns,
  TLS/SRTP posture, network isolation, monitoring, upgrade policy, and rollback
  plan.
- Add open config templates under `infra/managed-sip-edge/` for Asterisk,
  firewall notes, health checks, and customer trunk examples. Templates must
  contain placeholders, never real domains, IPs, phone numbers, secrets, or
  customer identifiers.
- Include customer recipes for FreePBX/Issabel, 3CX generic SIP trunk, Cisco
  CUCM route pattern, and generic cloud PBX SIP trunk.
- Include Spain-specific launch notes: +34 KYC, registered CLI, geographic
  number address requirements, and Orden TDF/149/2025 CLI restrictions. These
  are docs with verification dates, not code constants.
- Add contract tests proving the blueprint exists, references ADR 0010 and ADR
  0012, contains no obvious secrets or internal URLs, and covers every customer
  recipe above.
- Define handoff to platform work without importing platform code into this
  repository.

## Files to create/modify

- `docs/managed-sip-edge.md` - blueprint and runbook.
- `infra/managed-sip-edge/` - open templates only.
- `tests/test_product_docs.py` or a new doc contract test - blueprint coverage.
- `docs/telephony-connectivity.md` - only if execution discovers a verified
  update.
- This card file.

## Chips

- [ ] **C1 - Blueprint contract test.** Write a failing doc test first that
  asserts the managed edge blueprint references ADR 0010, ADR 0012, Asterisk
  22 LTS, FreePBX, Issabel, 3CX, CUCM, and Spain CLI/KYC notes. Files:
  `tests/test_product_docs.py`, `docs/managed-sip-edge.md`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_product_docs.py -q`
  -> all pass.
- [ ] **C2 - Open edge templates.** Add placeholder-only Asterisk and network
  templates plus a test that fails on secret-shaped values, internal domains,
  or real phone numbers. Files: `infra/managed-sip-edge/`,
  `tests/test_product_docs.py`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_product_docs.py -q`
  -> all pass.
- [ ] **C3 - Customer routing recipes.** Add step-by-step recipes for FreePBX,
  Issabel, 3CX, CUCM, and generic cloud PBX routing to the managed edge. Files:
  `docs/managed-sip-edge.md`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_product_docs.py -q`
  -> all pass.
- [ ] **C4 - Platform boundary and operations.** Add monitoring, upgrade,
  rollback, billing handoff, and the explicit open-core boundary. Files:
  `docs/managed-sip-edge.md`. Verify:
  `docker compose run --rm lucy-api pytest tests/test_product_docs.py -q`
  -> all pass.
- [ ] **C5 - Full gates and bookkeeping.** Run Docker pytest, lint, format,
  mypy, record evidence, and move this card. Verify:
  `docker compose run --rm lucy-api pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks; this card is docs/templates, and any
  executable checks must read committed artifacts directly.
- Do not hardcode real domains, IPs, phone numbers, credentials, provider
  names, quotas, regions, currencies, or latency budgets outside documented
  placeholders, typed settings, registries, or named constants.
- Do not add platform implementation code or import platform packages into the
  open SDK.
- Do not claim regulatory or provider facts without a verification date.

## Definition of Done

- [ ] `docker compose run --rm lucy-api pytest tests/test_product_docs.py -q`
  -> managed edge doc contract tests pass
- [ ] `docker compose run --rm lucy-api ruff check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api ruff format --check src tests` -> exit 0
- [ ] `docker compose run --rm lucy-api mypy src` -> exit 0
- [ ] `docker compose run --rm lucy-api pytest -q` -> full suite green
- [ ] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a required recipe cannot be verified, a template needs real secrets, or the
open/platform boundary is ambiguous, do NOT move the card to `done/`. Record the
blocker and raise a follow-up before publishing the blueprint.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item. -->

## Review evidence

<!-- Required before moving to done/ for cards >= 61 (review gate, card 61). -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
