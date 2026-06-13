# ADR 0006 - Multi-Tenant Fine-Tuning Via LoRA Adapters

## Status

Accepted

## Context

The hosted tier offers fine-tuned responses per customer with a local LLM included in
the service, on a small base model (for example ≤40B, per ADR 0005). Serving a full
fine-tuned model per customer does not scale on GPU. Lucy needs multi-tenant
economics without sacrificing per-customer customization.

## Decision

The hosted tier serves a single shared base model (≤40B, per ADR 0005) plus a
per-tenant LoRA adapter hot-loaded at request time. The LoRA adapter is the unit of
customization.

The default is shared base plus LoRA. A dedicated per-tenant model is an explicit
enterprise exception, not the default.

Tenant-to-adapter routing is explicit and observable. Adapters are tracked in an
adapter registry, analogous to the model registry in `src/lucy/providers.py`. Tenant
isolation is mandatory: adapters and tenant data must not leak across tenants.

## Consequences

- An adapter registry and tenant-to-adapter routing become part of the control plane,
  kept explicit and observable.
- GPU footprint scales with one shared base plus many small adapters, not many full
  models.
- Builds on ADR 0005; base model, adapters, and routing stay config-driven against
  the registry, with no hardcoded names.
