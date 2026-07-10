"""Composable, voice-native AgentGraph applications."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from lucy.clock import Clock
from lucy.graph import END, AgentGraph, CompiledAgentGraph
from lucy.llm import LlmProvider
from lucy.nodes import (
    ContextSynthesisConfig,
    ContextSynthesisNode,
    DisclosureConfig,
    DisclosureNode,
    DispositionConfig,
    DispositionNode,
    DtmfMenuConfig,
    DtmfMenuNode,
    EndCallConfig,
    EndCallNode,
    FunnelClassifierConfig,
    FunnelClassifierNode,
    IntentRouterConfig,
    IntentRouterNode,
    LlmNode,
    LlmNodeConfig,
    NodeConfig,
    SayNode,
    SentimentConfig,
    SentimentNode,
    SlotFillerConfig,
    SlotFillerNode,
    SlotSpec,
    SummaryConfig,
    SummaryNode,
    TransferConfig,
    TransferNode,
)
from lucy.providers import ModelRegistry
from lucy.rag import SpeculativeRagNode
from lucy.runtime import TurnContext
from lucy.settings import LatencyBudgets
from lucy.specs import FunnelStage
from lucy.state import CheckpointStore, ConversationState


class _PrebuiltConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class BookingAgentConfig(_PrebuiltConfig):
    disclosure: DisclosureConfig
    slots: List[SlotSpec]
    intents: Dict[str, str]
    outcome_by_intent: Dict[str, str]
    say_by_intent: Dict[str, str] = Field(default_factory=dict)
    escalation_transfer: Optional[TransferConfig] = None
    context: ContextSynthesisConfig = Field(default_factory=ContextSynthesisConfig)
    slot_filler: SlotFillerConfig = Field(default_factory=SlotFillerConfig)
    router: IntentRouterConfig = Field(default_factory=IntentRouterConfig)
    generation: LlmNodeConfig = Field(default_factory=LlmNodeConfig)
    funnel: FunnelClassifierConfig = Field(default_factory=FunnelClassifierConfig)


class LeadQualifierConfig(_PrebuiltConfig):
    slots: List[SlotSpec]
    disposition_by_stage: Dict[FunnelStage, str]
    default_disposition: str
    end_call: EndCallConfig
    slot_filler: SlotFillerConfig = Field(default_factory=SlotFillerConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    funnel: FunnelClassifierConfig = Field(default_factory=FunnelClassifierConfig)


class ReceptionistConfig(_PrebuiltConfig):
    disclosure: DisclosureConfig
    intents: Dict[str, str]
    departments: Dict[str, TransferConfig]
    confidence_floor: float = Field(ge=0.0, le=1.0)
    dtmf: DtmfMenuConfig
    router: IntentRouterConfig = Field(default_factory=IntentRouterConfig)


class SurveyQuestion(_PrebuiltConfig):
    slot: str
    prompt: str
    pattern: str
    confirm_template: Optional[str] = None


class SurveyAgentConfig(_PrebuiltConfig):
    questions: List[SurveyQuestion] = Field(min_length=1)
    completion_template: str
    disposition: DispositionConfig
    slot_filler: SlotFillerConfig = Field(default_factory=SlotFillerConfig)
    summary: SummaryConfig = Field(default_factory=SummaryConfig)


def _add_node(
    graph: AgentGraph[ConversationState],
    name: str,
    node: object,
) -> None:
    config = getattr(node, "config")
    if not isinstance(config, NodeConfig):
        raise TypeError("prebuilt node config must inherit NodeConfig")
    fallback = getattr(node, "fallback", None)
    graph.add_node(
        name,
        node,  # type: ignore[arg-type]
        deadline_ms=config.deadline_ms,
        retries=config.retries,
        fallback=fallback if callable(fallback) else None,
    )


def booking_agent(
    *,
    llm: LlmProvider,
    registry: ModelRegistry,
    provider: str,
    model: str,
    rag: SpeculativeRagNode,
    clock: Clock,
    budgets: LatencyBudgets,
    config: BookingAgentConfig,
    checkpointer: Optional[CheckpointStore] = None,
) -> CompiledAgentGraph[ConversationState]:
    """Build the booking graph used by the six golden sales scenarios."""
    missing = set(config.intents) - set(config.outcome_by_intent)
    if missing:
        raise ValueError("booking outcomes missing intents: %s" % sorted(missing))
    unknown_say = set(config.say_by_intent) - set(config.intents)
    if unknown_say:
        raise ValueError(
            "booking say routes have unknown intents: %s" % sorted(unknown_say)
        )

    graph: AgentGraph[ConversationState] = AgentGraph()
    disclosure = DisclosureNode(config.disclosure)
    context = ContextSynthesisNode(rag, config=config.context)
    slots = SlotFillerNode(config.slots, config.slot_filler)
    router = IntentRouterNode(
        config.intents, llm, registry, provider, model, config.router
    )
    generation = LlmNode(
        llm,
        registry,
        provider,
        model,
        clock,
        budgets,
        config.generation,
    )
    funnel = FunnelClassifierNode(llm, registry, provider, model, config.funnel)

    _add_node(graph, "disclosure", disclosure)
    _add_node(graph, "context_synthesis", context)
    _add_node(graph, "slot_filler", slots)
    _add_node(graph, "intent_router", router)
    _add_node(graph, "llm", generation)
    _add_node(graph, "funnel_classifier", funnel)

    generation_nodes = ["llm"]
    for intent, template in config.say_by_intent.items():
        name = "say_%s" % intent
        _add_node(graph, name, SayNode(template))
        generation_nodes.append(name)

    async def outcome(state: ConversationState, ctx: TurnContext) -> Dict[str, object]:
        intent = str(state.agent_state["intent"])
        actual = config.outcome_by_intent[intent]
        user_text = str(ctx.payload.get("user_text", ""))
        agent_state = {
            **state.agent_state,
            "actual_outcome": actual,
            "rag_grounded": bool(state.agent_state.get("grounding_ids"))
            or not user_text.strip(),
            "policy_adhered": not bool(
                state.agent_state.get("guardrail", {}).get("blocked", False)
            ),
        }
        return {"agent_state": agent_state, "turns": state.turns + 1}

    graph.add_node("outcome", outcome)
    if config.escalation_transfer is not None:
        _add_node(
            graph,
            "transfer_escalation",
            TransferNode(config.escalation_transfer),
        )
        graph.add_edge("transfer_escalation", END)
    graph.add_edge("disclosure", "context_synthesis")
    graph.add_edge("context_synthesis", "slot_filler")
    graph.add_edge("slot_filler", "intent_router")
    graph.add_conditional_edge(
        "intent_router",
        lambda state: (
            "say_%s" % state.agent_state["intent"]
            if state.agent_state["intent"] in config.say_by_intent
            else "llm"
        ),
    )
    for name in generation_nodes:
        graph.add_edge(name, "funnel_classifier")
    graph.add_edge("funnel_classifier", "outcome")
    graph.add_conditional_edge(
        "outcome",
        lambda state: (
            "transfer_escalation"
            if state.agent_state["actual_outcome"] == "escalation"
            and config.escalation_transfer is not None
            else END
        ),
    )
    return graph.set_entry("disclosure").compile(checkpointer=checkpointer)


def lead_qualifier(
    *,
    llm: LlmProvider,
    registry: ModelRegistry,
    provider: str,
    model: str,
    config: LeadQualifierConfig,
    checkpointer: Optional[CheckpointStore] = None,
) -> CompiledAgentGraph[ConversationState]:
    """Build a qualification graph with sentiment, funnel, and call closure."""
    graph: AgentGraph[ConversationState] = AgentGraph()
    _add_node(
        graph,
        "slot_filler",
        SlotFillerNode(config.slots, config.slot_filler),
    )
    _add_node(
        graph,
        "sentiment",
        SentimentNode(llm, registry, provider, model, config.sentiment),
    )
    _add_node(
        graph,
        "funnel_classifier",
        FunnelClassifierNode(llm, registry, provider, model, config.funnel),
    )

    async def disposition(
        state: ConversationState, ctx: TurnContext
    ) -> Dict[str, object]:
        value = (
            config.disposition_by_stage.get(
                state.funnel_stage, config.default_disposition
            )
            if state.funnel_stage is not None
            else config.default_disposition
        )
        return {"agent_state": {**state.agent_state, "disposition": value}}

    graph.add_node("disposition", disposition)
    _add_node(graph, "end_call", EndCallNode(config.end_call))
    graph.add_edge("slot_filler", "sentiment")
    graph.add_edge("sentiment", "funnel_classifier")
    graph.add_edge("funnel_classifier", "disposition")
    graph.add_edge("disposition", "end_call")
    graph.add_edge("end_call", END)
    return graph.set_entry("slot_filler").compile(checkpointer=checkpointer)


def receptionist(
    *,
    llm: LlmProvider,
    registry: ModelRegistry,
    provider: str,
    model: str,
    config: ReceptionistConfig,
    checkpointer: Optional[CheckpointStore] = None,
) -> CompiledAgentGraph[ConversationState]:
    """Build an intent-first receptionist with confidence-based IVR fallback."""
    if set(config.departments) != set(config.intents):
        raise ValueError("receptionist departments must match configured intents")
    unknown_options = set(config.dtmf.options.values()) - set(config.departments)
    if unknown_options:
        raise ValueError("DTMF options reference unknown departments")

    graph: AgentGraph[ConversationState] = AgentGraph()
    _add_node(graph, "disclosure", DisclosureNode(config.disclosure))
    _add_node(
        graph,
        "intent_router",
        IntentRouterNode(config.intents, llm, registry, provider, model, config.router),
    )
    _add_node(graph, "dtmf_menu", DtmfMenuNode(config.dtmf))
    for intent, transfer_config in config.departments.items():
        name = "transfer_%s" % intent
        _add_node(graph, name, TransferNode(transfer_config))
        graph.add_edge(name, END)

    graph.add_edge("disclosure", "intent_router")
    graph.add_conditional_edge(
        "intent_router",
        lambda state: (
            "dtmf_menu"
            if float(state.agent_state["intent_confidence"]) < config.confidence_floor
            else "transfer_%s" % state.agent_state["intent"]
        ),
    )
    graph.add_conditional_edge(
        "dtmf_menu",
        lambda state: (
            "transfer_%s" % state.agent_state["dtmf_selection"]
            if state.agent_state["dtmf_selection"] in config.departments
            else END
        ),
    )
    return graph.set_entry("disclosure").compile(checkpointer=checkpointer)


class _SurveyStepNode:
    name = "survey_step"

    def __init__(self, config: SurveyAgentConfig) -> None:
        self.config = config.slot_filler
        self.survey_config = config

    async def __call__(
        self, state: ConversationState, ctx: TurnContext
    ) -> Dict[str, object]:
        index = int(state.agent_state.get("survey_index", 0))
        if not state.agent_state.get("survey_started"):
            spoken = await SayNode(self.survey_config.questions[0].prompt)(state, ctx)
            return {
                **spoken,
                "agent_state": {
                    **state.agent_state,
                    "survey_started": True,
                    "survey_index": 0,
                    "survey_complete": False,
                    "survey_post_call": False,
                },
            }

        question = self.survey_config.questions[index]
        filled = await SlotFillerNode(
            [
                SlotSpec(
                    name=question.slot,
                    pattern=question.pattern,
                    required=True,
                    confirm_template=question.confirm_template,
                )
            ],
            self.survey_config.slot_filler,
        )(state, ctx)
        merged = state.merged(filled)
        if question.slot not in merged.slots:
            repeated = await SayNode(question.prompt)(merged, ctx)
            return {**filled, **repeated}

        next_index = index + 1
        complete = next_index == len(self.survey_config.questions)
        template = (
            self.survey_config.completion_template
            if complete
            else self.survey_config.questions[next_index].prompt
        )
        spoken = await SayNode(template)(merged, ctx)
        return {
            **filled,
            **spoken,
            "agent_state": {
                **state.agent_state,
                "survey_started": True,
                "survey_index": min(next_index, len(self.survey_config.questions) - 1),
                "survey_complete": complete,
                "survey_post_call": complete
                and ctx.payload.get("session_ended") is True,
            },
        }


def survey_agent(
    *,
    llm: LlmProvider,
    registry: ModelRegistry,
    provider: str,
    model: str,
    config: SurveyAgentConfig,
    checkpointer: Optional[CheckpointStore] = None,
) -> CompiledAgentGraph[ConversationState]:
    """Build a resumable question sequence with an explicit post-call tail."""
    graph: AgentGraph[ConversationState] = AgentGraph()
    step = _SurveyStepNode(config)
    _add_node(graph, "survey_step", step)
    _add_node(
        graph,
        "summary",
        SummaryNode(llm, registry, provider, model, config.summary),
    )
    _add_node(graph, "disposition", DispositionNode(config.disposition))
    graph.add_conditional_edge(
        "survey_step",
        lambda state: "summary" if state.agent_state.get("survey_post_call") else END,
    )
    graph.add_edge("summary", "disposition")
    graph.add_edge("disposition", END)
    return graph.set_entry("survey_step").compile(checkpointer=checkpointer)


__all__ = [
    "BookingAgentConfig",
    "LeadQualifierConfig",
    "ReceptionistConfig",
    "SurveyQuestion",
    "SurveyAgentConfig",
    "booking_agent",
    "lead_qualifier",
    "receptionist",
    "survey_agent",
]
