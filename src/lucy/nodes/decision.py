"""Decision, routing, guardrail, and disclosure nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from lucy.llm import LlmMessage, LlmProvider, LlmRequest, resolve_llm
from lucy.nodes.base import NodeConfig, StateUpdate, collect_llm_text
from lucy.providers import ModelRegistry
from lucy.runtime import TurnContext
from lucy.state import ConversationState
from lucy.transport.schema import TtsSpeak

INTENT_PROMPT = "Return JSON with exactly one configured intent label."


class IntentRouterConfig(NodeConfig):
    deadline_ms: int = 150


class GuardrailConfig(NodeConfig):
    deadline_ms: int = 20
    position: Literal["pre", "post"]


class DisclosureConfig(NodeConfig):
    deadline_ms: int = 20
    template: str
    once_per_call: bool = True


@dataclass(frozen=True)
class GuardrailPolicy:
    blocked_terms: Sequence[str]
    fallback_text: str


class GuardrailVerdict(BaseModel):
    blocked: bool
    matched: Optional[str] = None
    replacement: Optional[str] = None


class _IntentPayload(BaseModel):
    intent: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class IntentRouterNode:
    name = "intent_router"

    def __init__(
        self,
        intents: Mapping[str, str],
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        config: Optional[IntentRouterConfig] = None,
    ) -> None:
        info = resolve_llm(registry, provider, model)
        if not info.low_latency:
            raise ValueError(
                "intent router model %s/%s must have low_latency=True"
                % (provider, model)
            )
        if not intents:
            raise ValueError("intent router requires at least one intent")
        self.intents: Dict[str, str] = dict(intents)
        self.llm = llm
        self.provider = provider
        self.model = model
        self.config = config or IntentRouterConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        raw = await collect_llm_text(
            self.llm,
            LlmRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    LlmMessage(
                        role="user",
                        content="%s\n\nIntents: %s\n\nUtterance: %s"
                        % (
                            INTENT_PROMPT,
                            json.dumps(self.intents, sort_keys=True),
                            ctx.payload.get("user_text", ""),
                        ),
                    )
                ],
            ),
        )
        payload = _IntentPayload.model_validate(json.loads(raw))
        if payload.intent not in self.intents:
            raise ValueError("LLM returned unknown intent: %s" % payload.intent)
        return {
            "agent_state": {
                **state.agent_state,
                "intent": payload.intent,
                "intent_confidence": payload.confidence,
            }
        }


class GuardrailNode:
    name = "guardrail"

    def __init__(
        self,
        policy: GuardrailPolicy,
        config: GuardrailConfig,
    ) -> None:
        self.policy = policy
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if self.config.position == "pre":
            text = str(ctx.payload.get("user_text", ""))
        else:
            text = str(state.agent_state.get("assistant_draft", ""))
        normalized = text.casefold()
        matched = next(
            (
                term
                for term in self.policy.blocked_terms
                if term.casefold() in normalized
            ),
            None,
        )
        verdict = GuardrailVerdict(
            blocked=matched is not None,
            matched=matched,
            replacement=self.policy.fallback_text if matched is not None else None,
        )
        agent_state = {
            **state.agent_state,
            "guardrail": verdict.model_dump(mode="json"),
        }
        if self.config.position == "post" and matched is not None:
            agent_state["assistant_draft"] = self.policy.fallback_text
        return {"agent_state": agent_state}


class DisclosureNode:
    name = "disclosure"

    def __init__(self, config: DisclosureConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if self.config.once_per_call and state.agent_state.get("disclosed"):
            return {"agent_state": dict(state.agent_state)}
        if ctx.emit is not None:
            ctx.emit(
                TtsSpeak(
                    utterance_id="disclosure-%s" % (ctx.turn_id or "turn"),
                    text=self.config.template,
                )
            )
        return {
            "agent_state": {
                **state.agent_state,
                "disclosed": True,
            }
        }
