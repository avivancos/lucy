"""Perception and context-synthesis nodes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from lucy.llm import LlmMessage, LlmProvider, LlmRequest, resolve_llm
from lucy.metrics import FunnelEvent, SentimentScore
from lucy.nodes.base import NodeConfig, StateUpdate, collect_llm_text
from lucy.observe import Tracer, get_tracer
from lucy.providers import ModelRegistry
from lucy.rag import (
    RagResult,
    SpeculativeRagNode,
    emit_rag_retrieval_span,
    grounded_context_message,
)
from lucy.runtime import TurnContext
from lucy.specs import FunnelStage, SentimentLabel
from lucy.state import ConversationState
from lucy.transport.schema import SessionConfigure, TtsSpeak

CONTEXT_SYNTHESIS_PROMPT = (
    "Synthesize a concise context for the next voice response. "
    "Preserve every grounding identifier."
)
SENTIMENT_PROMPT = (
    "Return JSON with label positive, neutral, or negative and confidence 0..1."
)
FUNNEL_PROMPT = "Return JSON with a valid funnel stage and confidence 0..1."


class ContextSynthesisConfig(NodeConfig):
    deadline_ms: int = 100
    max_chunks: int = Field(default=8, ge=1)


class SlotFillerConfig(NodeConfig):
    deadline_ms: int = 20


class SentimentConfig(NodeConfig):
    deadline_ms: int = 200


class FunnelClassifierConfig(NodeConfig):
    deadline_ms: int = 200


class LanguageDetectConfig(NodeConfig):
    deadline_ms: int = 20
    supported_locales: List[str]
    min_confidence: float = Field(ge=0.0, le=1.0)
    stt_by_locale: Dict[str, str]
    tts_by_locale: Dict[str, str]
    vad: str


@dataclass(frozen=True)
class SlotSpec:
    name: str
    pattern: str
    required: bool = False
    confirm_template: Optional[str] = None


class _SentimentPayload(BaseModel):
    label: SentimentLabel
    confidence: float = Field(ge=0.0, le=1.0)


class _FunnelPayload(BaseModel):
    stage: FunnelStage
    confidence: float = Field(ge=0.0, le=1.0)


def _retrieval_update(state: ConversationState, result: RagResult) -> StateUpdate:
    return {
        "agent_state": {
            **state.agent_state,
            "prompt_context": result.prompt_context,
            "grounding_ids": [chunk.grounding_id for chunk in result.chunks],
            "rag_deadline_exceeded": result.deadline_exceeded,
        }
    }


def _limit_result(result: RagResult, max_chunks: int) -> RagResult:
    return RagResult(
        query=result.query,
        chunks=result.chunks[:max_chunks],
        cache_hit=result.cache_hit,
        deadline_exceeded=result.deadline_exceeded,
    )


class ContextSynthesisNode:
    name = "context_synthesis"

    def __init__(
        self,
        rag: SpeculativeRagNode,
        llm: Optional[LlmProvider] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        config: Optional[ContextSynthesisConfig] = None,
        tracer: Optional[Tracer] = None,
    ) -> None:
        if llm is not None and (not provider or not model):
            raise ValueError("LLM context synthesis requires provider and model")
        self.rag = rag
        self.llm = llm
        self.provider = provider
        self.model = model
        self.config = config or ContextSynthesisConfig()
        self.tracer = tracer

    async def _retrieve(self, ctx: TurnContext) -> RagResult:
        active_tracer = self.tracer if self.tracer is not None else get_tracer()
        started_at_ms = active_tracer.now_ms()
        result = _limit_result(
            await self.rag.prefetch(str(ctx.payload.get("user_text", ""))),
            self.config.max_chunks,
        )
        emit_rag_retrieval_span(
            active_tracer,
            result,
            session_id=ctx.session_id,
            turn_id=ctx.turn_id or None,
            started_at_ms=started_at_ms,
            ended_at_ms=active_tracer.now_ms(),
            included_grounding_ids=[chunk.grounding_id for chunk in result.chunks],
        )
        return result

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        result = await self._retrieve(ctx)
        update = _retrieval_update(state, result)
        if self.llm is None or result.deadline_exceeded or not result.chunks:
            return update

        context_message = grounded_context_message(result)
        assert context_message is not None
        transcript = "\n".join(
            "%s: %s" % (line.speaker, line.text) for line in state.transcript
        )
        synthesized = await collect_llm_text(
            self.llm,
            LlmRequest(
                provider=str(self.provider),
                model=str(self.model),
                messages=[
                    context_message,
                    LlmMessage(
                        role="user",
                        content="%s\n\nTranscript:\n%s"
                        % (CONTEXT_SYNTHESIS_PROMPT, transcript),
                    ),
                ],
            ),
        )
        grounding_ids = [chunk.grounding_id for chunk in result.chunks]
        if synthesized and all(item in synthesized for item in grounding_ids):
            update["agent_state"]["prompt_context"] = synthesized
        return update

    async def fallback(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        result = await self._retrieve(ctx)
        return _retrieval_update(state, result)


class SlotFillerNode:
    name = "slot_filler"

    def __init__(
        self,
        slots: Sequence[SlotSpec],
        config: Optional[SlotFillerConfig] = None,
    ) -> None:
        self.slots = list(slots)
        self.config = config or SlotFillerConfig()
        self._patterns = {slot.name: re.compile(slot.pattern) for slot in slots}

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        text = str(ctx.payload.get("user_text", ""))
        filled: Dict[str, str] = {}
        for slot in self.slots:
            match = self._patterns[slot.name].search(text)
            if match is None:
                continue
            value = match.group(1) if match.lastindex else match.group(0)
            filled[slot.name] = value
            if slot.confirm_template is not None and ctx.emit is not None:
                values = {**state.slots, **filled}
                ctx.emit(
                    TtsSpeak(
                        utterance_id="slot-%s-%s" % (slot.name, ctx.turn_id or "turn"),
                        text=slot.confirm_template.format(**values),
                    )
                )
        return {"slots": {**state.slots, **filled}}


class SentimentNode:
    name = "sentiment"

    def __init__(
        self,
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        config: Optional[SentimentConfig] = None,
    ) -> None:
        resolve_llm(registry, provider, model)
        self.llm = llm
        self.provider = provider
        self.model = model
        self.config = config or SentimentConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        raw = await collect_llm_text(
            self.llm,
            LlmRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    LlmMessage(
                        role="user",
                        content="%s\n\n%s"
                        % (SENTIMENT_PROMPT, ctx.payload.get("user_text", "")),
                    )
                ],
            ),
        )
        payload = _SentimentPayload.model_validate(json.loads(raw))
        score = SentimentScore(
            label=payload.label,
            confidence=payload.confidence,
            model="%s/%s" % (self.provider, self.model),
        )
        return {
            "agent_state": {
                **state.agent_state,
                "sentiment": score.model_dump(mode="json"),
            }
        }


class FunnelClassifierNode:
    name = "funnel_classifier"

    def __init__(
        self,
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        config: Optional[FunnelClassifierConfig] = None,
    ) -> None:
        resolve_llm(registry, provider, model)
        self.llm = llm
        self.provider = provider
        self.model = model
        self.config = config or FunnelClassifierConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        raw = await collect_llm_text(
            self.llm,
            LlmRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    LlmMessage(
                        role="user",
                        content="%s\n\n%s"
                        % (FUNNEL_PROMPT, ctx.payload.get("user_text", "")),
                    )
                ],
            ),
        )
        payload = _FunnelPayload.model_validate(json.loads(raw))
        event = FunnelEvent(
            session_id=ctx.session_id,
            stage=payload.stage,
            confidence=payload.confidence,
            crm_payload=dict(state.slots),
        )
        if ctx.emit is not None:
            ctx.emit(event)
        return {"funnel_stage": payload.stage}


class LanguageDetectNode:
    name = "language_detect"

    def __init__(self, config: LanguageDetectConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        locale = str(ctx.payload.get("detected_locale", ""))
        confidence = float(ctx.payload.get("language_confidence", 0.0))
        current = str(state.agent_state.get("locale", ""))
        if (
            locale not in self.config.supported_locales
            or confidence < self.config.min_confidence
            or locale == current
        ):
            return {"agent_state": dict(state.agent_state)}
        stt = self.config.stt_by_locale.get(locale)
        tts = self.config.tts_by_locale.get(locale)
        if stt is None or tts is None:
            return {"agent_state": dict(state.agent_state)}
        if ctx.emit is not None:
            ctx.emit(SessionConfigure(stt=stt, tts=tts, vad=self.config.vad))
        return {"agent_state": {**state.agent_state, "locale": locale}}
