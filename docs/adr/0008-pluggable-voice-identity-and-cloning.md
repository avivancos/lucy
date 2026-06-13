# ADR 0008 - Pluggable Voice Identity And Cloning

## Status

Accepted

## Context

Lucy models voice modulation (`VoiceModulationSpec` in `src/lucy/specs.py`) but has no
concept of voice identity or voice cloning. The product needs cloned voices with a
pluggable engine: a self-hosted open engine by default in the hosted tier, and a SaaS
engine optional per tenant.

## Decision

The voice boundary (`VoiceSpec` and `VoiceModulationSpec` in `src/lucy/specs.py`) is
extended with a voice identity: a `voice_id` plus an enrollment reference. The TTS
adapter stays engine-agnostic.

Engines are pluggable. A self-hosted open cloning engine is the default for the hosted
tier, with no network hop. A SaaS cloning provider (for example ElevenLabs or Cartesia
PVC) is optional per tenant.

Cloned voices require recorded consent. Enrollment artifacts are governed as PII,
consistent with `ObservabilitySpec.redact_pii`. Voice ids and engine selection live in
the registry and typed settings, never hardcoded.

## Consequences

- `src/lucy/specs.py` voice models gain a voice identity; the TTS boundary stays
  provider-agnostic.
- Consent records and enrollment storage become governed PII surfaces.
- The self-hosted default keeps `transport_ms` low for the hosted tier, while per-
  tenant SaaS remains available.
