# ADR 0007 - On-The-Fly Context Synthesis

## Status

Accepted

## Context

Lucy already retrieves context with a hybrid index and a `SpeculativeRagNode`
(prefetch, cache, `deadline_ms`) in `src/lucy/rag.py`, and it is already LangChain-
free. The product needs to generate fresh context on the fly during a turn, not only
retrieve it, using the pluggable offline model from ADR 0005.

## Decision

A context-synthesis node runs inside the `GraphExecutor` (`src/lucy/runtime.py`),
deadline-bounded, using the pluggable offline model from ADR 0005 to generate fresh
grounded context from retrieval (`src/lucy/rag.py`) plus the live transcript.

There is no orchestration framework: Lucy does not adopt LangChain or LlamaIndex.
Context assembly is explicit and reuses `SpeculativeRagNode` prefetch and
`RagResult.prompt_context` with `grounding_id` citations.

The node is deadline-first: on timeout it falls back to retrieval-only context
(`deadline_exceeded`) and never blocks the turn. Its cost is gated on `rag_ms` in the
`LatencyWaterfall`. Synthesis is speculative and is triggered on partial transcripts.

## Consequences

- The synthesis node is a `GraphNode` with its own deadline and fallback in
  `src/lucy/runtime.py`.
- Grounding ids are preserved end to end so responses stay citable.
- Builds on ADR 0005 and reuses the `src/lucy/rag.py` boundary; the retrieval-only
  path remains the safe fallback.
