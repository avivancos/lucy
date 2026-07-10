"""Post-call summarization, CRM synchronization, disposition, and eval nodes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Optional

from lucy.evals import EvalEvidence, SyntheticCallScenario, score_synthetic_call
from lucy.llm import LlmMessage, LlmProvider, LlmRequest, resolve_llm
from lucy.metrics import CrmMetricEvent, FunnelEvent, SentimentScore
from lucy.nodes.base import NodeConfig, StateUpdate, collect_llm_text
from lucy.providers import ModelRegistry
from lucy.runtime import TurnContext
from lucy.specs import FunnelStage
from lucy.state import ConversationState
from lucy.tools import McpToolExecutor, ToolDef

SUMMARY_PROMPT = "Summarize the completed call using only the transcript."


class PostCallOnlyError(RuntimeError):
    """Raised when a post-call node is invoked during a live session."""


class SummaryConfig(NodeConfig):
    deadline_ms: int = 500


class CrmSyncConfig(NodeConfig):
    deadline_ms: int = 1_000
    lead_id_slot: str


class DispositionConfig(NodeConfig):
    deadline_ms: int = 20
    mapping: Dict[FunnelStage, str]
    default: str


class EvalHookConfig(NodeConfig):
    deadline_ms: int = 100


def _require_post_call(ctx: TurnContext) -> None:
    if ctx.payload.get("session_ended") is not True:
        raise PostCallOnlyError("post-call nodes require an ended session")


class SummaryNode:
    name = "summary"

    def __init__(
        self,
        llm: LlmProvider,
        registry: ModelRegistry,
        provider: str,
        model: str,
        config: Optional[SummaryConfig] = None,
    ) -> None:
        resolve_llm(registry, provider, model)
        self.llm = llm
        self.provider = provider
        self.model = model
        self.config = config or SummaryConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        _require_post_call(ctx)
        transcript = "\n".join(
            "%s: %s" % (line.speaker, line.text) for line in state.transcript
        )
        summary = await collect_llm_text(
            self.llm,
            LlmRequest(
                provider=self.provider,
                model=self.model,
                messages=[
                    LlmMessage(
                        role="system",
                        content=SUMMARY_PROMPT,
                    ),
                    LlmMessage(role="user", content=transcript),
                ],
            ),
        )
        return {"agent_state": {**state.agent_state, "summary": summary}}


class CrmSyncNode:
    name = "crm_sync"

    def __init__(
        self,
        tool: ToolDef,
        executor: McpToolExecutor,
        config: CrmSyncConfig,
    ) -> None:
        self.tool = tool
        self.executor = executor
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        _require_post_call(ctx)
        if state.funnel_stage is None:
            raise ValueError("CRM sync requires a funnel stage")
        lead_id = state.slots.get(self.config.lead_id_slot)
        if not lead_id:
            raise ValueError("CRM sync requires a lead id")
        sentiment = SentimentScore.model_validate(state.agent_state.get("sentiment"))
        funnel = FunnelEvent(
            session_id=ctx.session_id,
            stage=state.funnel_stage,
            confidence=1.0,
            crm_payload=state.slots,
        )
        event = CrmMetricEvent(
            session_id=ctx.session_id,
            lead_id=lead_id,
            sentiment=sentiment,
            funnel=funnel,
            emitted_at_ms=int(ctx.clock.monotonic() * 1_000),
        )
        result = await self.executor.execute(self.tool, event.crm_ready_payload)
        return {"tool_results": [*state.tool_results, asdict(result)]}


class DispositionNode:
    name = "disposition"

    def __init__(self, config: DispositionConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        _require_post_call(ctx)
        disposition = (
            self.config.mapping.get(state.funnel_stage, self.config.default)
            if state.funnel_stage is not None
            else self.config.default
        )
        return {"agent_state": {**state.agent_state, "disposition": disposition}}


class EvalHookNode:
    name = "eval_hook"

    def __init__(self, config: Optional[EvalHookConfig] = None) -> None:
        self.config = config or EvalHookConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        _require_post_call(ctx)
        raw_scenario = state.agent_state.get("scenario")
        if raw_scenario is None:
            return {}
        scenario = SyntheticCallScenario.model_validate(raw_scenario)
        actual_outcome = (
            state.funnel_stage.value if state.funnel_stage is not None else "completed"
        )
        result = score_synthetic_call(
            scenario,
            EvalEvidence(
                actual_outcome=actual_outcome,
                rag_grounded=bool(state.agent_state.get("rag_grounded")),
                policy_adhered=bool(state.agent_state.get("policy_adhered")),
                interruption_handled=bool(
                    state.agent_state.get("interruption_handled")
                ),
                escalated_to_human=bool(state.agent_state.get("escalated_to_human")),
            ),
        )
        if ctx.emit is not None:
            ctx.emit(result)
        return {
            "agent_state": {
                **state.agent_state,
                "eval_result": result.model_dump(mode="json"),
            }
        }
