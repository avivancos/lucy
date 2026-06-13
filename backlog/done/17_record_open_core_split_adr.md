# 17 - Record open-core split ADR

**Epic:** Architecture
**Estimated effort:** ~2 h
**State:** done

## Goal

Lock the open/closed boundary so the SDK can be published and the platform
monetized without re-litigating what belongs where.

## Spec

ADR 0010 records: the guiding rule (in-process = open; cross-run/tenant
storage, aggregation, comparison = closed), the module-by-module split
(`lucy.observe`/`lucy.testing`/`lucy.serve` open; ingest, dashboard, evals at
scale, fleet, LoRA/voice hosting, managed SIP edge closed), Apache-2.0
licensing with trademark note, deferred naming, and Pili's exit to a private
vertical repo.

## Files to create/modify

- `docs/adr/0010-open-core-split.md` - the decision
- `LICENSE`, `NOTICE` - Apache-2.0 + trademark notice
- `tests/test_architecture_adrs.py` - contract tests for the new ADR and license

## Definition of Done

- [x] ADR 0010 accepted with boundary table and guiding rule.
- [x] LICENSE is the verbatim Apache-2.0 text; NOTICE withholds the marks.
- [x] Contract tests assert the ADR names the seam modules and the wire spec.
- [x] Targeted tests green (local venv; Docker daemon not running this session).
- [x] Post-task audit done

## Improvements noted

- Run the pre-publication scrub (operating docs to private, SPDX headers,
  TRADEMARKS.md) as its own card before any public release.
