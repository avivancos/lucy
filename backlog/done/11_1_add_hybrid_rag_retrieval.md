# 11_1 - Add hybrid RAG retrieval

**Epic:** RAG
**Estimated effort:** ~8 h
**State:** done

## Goal

Extend the local RAG primitive from lexical retrieval to hybrid lexical/vector
retrieval while keeping deadline-safe behavior.

## Spec

Add a local embedding backend or recorded embedding fixture path, combine lexical
and vector scores, preserve grounding metadata, and keep cache/deadline fallback
semantics unchanged.

## Files to create/modify

- `src/lucy/rag.py` - hybrid retriever
- `tests/test_rag.py` - hybrid retrieval tests

## Definition of Done

- [x] Hybrid retrieval combines lexical and vector signals.
- [x] Grounding ids remain stable.
- [x] Cache hit/miss and deadline fallback remain explicit.
- [x] Tests use local deterministic behavior.
- [x] Targeted tests green in local runtime.
- [x] Post-task audit done.

## Improvements noted

- Add external vector DB adapters through MCP once persistence and server
  lifecycle contracts are ready.
