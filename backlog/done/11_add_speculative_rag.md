# 11 - Add speculative RAG

**Epic:** RAG
**Estimated effort:** ~8 h
**State:** done

## Goal

Add low-latency RAG primitives that can prefetch context before the LLM fully
needs it.

## Spec

Implement retrieval sources, cache lookup, speculative prefetch, ranking,
compression, grounding metadata, deadline-aware fallback, and traceable context
inclusion.

## Files to create/modify

- `src/lucy/rag.py` - RAG contracts and local in-memory retriever
- `tests/test_rag.py` - retrieval, cache, and deadline tests

## Definition of Done

- [x] Cache hit/miss behavior is explicit.
- [x] Retrieved chunks include source, score, and grounding id.
- [x] Deadline fallback returns safe empty context.
- [x] Context included in prompts is traceable.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add hybrid lexical/vector retrieval once the provider registry includes local
  embedding backends.
- Docker Compose verification should be rerun once the Docker daemon is active.
