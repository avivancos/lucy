# 8 - Add model registry and provider contracts

**Epic:** Providers
**Estimated effort:** ~5 h
**State:** done

## Goal

Create a versioned provider/model registry so compatible models are not
hardcoded across the codebase.

## Spec

Add model capabilities for STT, LLM, realtime, TTS, embeddings, and rerankers.
Seed the registry with OpenAI, Deepgram, AssemblyAI, ElevenLabs, Cartesia,
Google, Anthropic, Mistral, and Groq entries.

## Files to create/modify

- `src/lucy/providers.py` - registry and capability contracts
- `tests/test_registry_mcp_metrics.py` - registry tests

## Definition of Done

- [x] Registry has a version date.
- [x] Models can be filtered by capability.
- [x] Model lookup works by provider and model id.
- [x] No model list is duplicated outside the registry.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Add automated provider-catalog revalidation once live provider metadata access
  is available.
- Docker Compose verification should be rerun once the Docker daemon is active.
