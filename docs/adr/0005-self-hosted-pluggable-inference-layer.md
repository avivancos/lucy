# ADR 0005 - Self-Hosted Pluggable Inference Layer

## Status

Accepted

## Context

Lucy's model boundary (`Capability`, `ModelInfo`, `ModelRegistry` in
`src/lucy/providers.py`) is SaaS-only today. The product needs to run its own open
models (for example Qwen or Gemma) for the hosted tier, and to let customers plug in
their own offline models. Most turn latency is network and external providers
(ADR 0001); running a model self-hosted removes the provider network round-trip that
dominates the `transport_ms` slice of the `LatencyWaterfall`.

## Decision

Self-hosted models plug in behind the same boundary as SaaS providers. The model
registry in `src/lucy/providers.py` gains a self-hosted dimension on `ModelInfo`, and
self-hosted models keep the existing capability vocabulary (stt, llm, realtime, tts,
embedding, reranker).

The layer is engine-agnostic: GPU serving (for example vLLM, SGLang, or
TensorRT-LLM) and edge or CPU serving (for example candle or llama.cpp) sit behind
the boundary, and Lucy does not hardcode an engine. Engine and model selection are
configuration validated against the registry.

The base-model size ceiling (for example ≤40B) and concrete model ids live in the
registry and in typed settings as named constants, never hardcoded in code or in this
ADR, per the operating rules in agents.md.

Running self-hosted collapses the provider network hop and is expected to reduce
`transport_ms` and `llm_ms`. Whether to deepen Rust in the serving data plane stays
gated on measurement, consistent with ADR 0001 and the data-plane boundary of
ADR 0004; the orchestration language is not the primary latency lever.

## Consequences

- `src/lucy/providers.py` grows a self-hosted flag and self-hosted model entries;
  provider selection stays provider-agnostic.
- The Python/Rust split for local serving follows ADR 0004 (data plane versus control
  plane) and remains explicit and observable.
- Engine and model choice remain configuration, validated against the registry, with
  no hardcoded names or budgets.
