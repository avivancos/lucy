"""AgentGraph: stateful per-turn cognition graphs over GraphExecutor."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    List,
    Optional,
    TypeVar,
    cast,
)

from lucy.drivers import TurnDriver, TurnDriverReport
from lucy.llm import LlmMessage
from lucy.rag import RagResult, SpeculativeRagNode, grounded_context_message
from lucy.runtime import (
    GraphContext,
    GraphExecutionError,
    GraphExecutor,
    GraphNode,
    TurnContext,
)
from lucy.settings import GraphLimits
from lucy.specs import FunnelStage
from lucy.state import (
    Checkpoint,
    CheckpointStore,
    ConversationState,
    TranscriptLine,
    checkpoint_id,
)
from lucy.transport.schema import TtsSpeak

END = "__end__"
RAG_RESULT_PAYLOAD_KEY = "_lucy_rag_result"
StateT = TypeVar("StateT", bound=ConversationState)
AgentNodeHandler = Callable[[StateT, TurnContext], Awaitable[Dict[str, Any]]]
RouteFn = Callable[[StateT], str]


class GraphValidationError(ValueError):
    """Raised when an AgentGraph cannot be compiled."""


class GraphRouteError(GraphExecutionError):
    """Raised when a conditional route returns an unknown node."""


class GraphCycleLimitExceeded(GraphExecutionError):
    """Raised when a turn exceeds the configured superstep cap."""


@dataclass
class _NodeSpec(Generic[StateT]):
    name: str
    handler: AgentNodeHandler[StateT]
    deadline_ms: int
    retries: int
    fallback: Optional[AgentNodeHandler[StateT]] = None


class AgentGraph(Generic[StateT]):
    def __init__(self) -> None:
        self._nodes: Dict[str, _NodeSpec[StateT]] = {}
        self._order: List[str] = []
        self._edges: Dict[str, List[str]] = {}
        self._conditional: Dict[str, RouteFn[StateT]] = {}
        self._entry: Optional[str] = None

    def add_node(
        self,
        name: str,
        handler: AgentNodeHandler[StateT],
        *,
        deadline_ms: int = 500,
        retries: int = 0,
        fallback: Optional[AgentNodeHandler[StateT]] = None,
    ) -> "AgentGraph[StateT]":
        if name in self._nodes:
            raise GraphValidationError("duplicate node name: %s" % name)
        self._nodes[name] = _NodeSpec(
            name=name,
            handler=handler,
            deadline_ms=deadline_ms,
            retries=retries,
            fallback=fallback,
        )
        self._order.append(name)
        return self

    def add_edge(self, source: str, target: str) -> "AgentGraph[StateT]":
        self._edges.setdefault(source, []).append(target)
        return self

    def add_conditional_edge(
        self, source: str, route_fn: RouteFn[StateT]
    ) -> "AgentGraph[StateT]":
        if source in self._conditional:
            raise GraphValidationError(
                "conditional edge already exists for source: %s" % source
            )
        self._conditional[source] = route_fn
        return self

    def set_entry(self, name: str) -> "AgentGraph[StateT]":
        self._entry = name
        return self

    def compile(
        self,
        checkpointer: Optional[CheckpointStore] = None,
        limits: Optional[GraphLimits] = None,
    ) -> "CompiledAgentGraph[StateT]":
        if self._entry is None or self._entry not in self._nodes:
            raise GraphValidationError("entry node is not set or is unknown")
        for source, targets in self._edges.items():
            if source not in self._nodes:
                raise GraphValidationError("unknown edge source: %s" % source)
            if source in self._conditional:
                raise GraphValidationError(
                    "source %s mixes static and conditional edges" % source
                )
            for target in targets:
                if target != END and target not in self._nodes:
                    raise GraphValidationError("unknown edge target: %s" % target)
        for source in self._conditional:
            if source not in self._nodes:
                raise GraphValidationError("unknown conditional source: %s" % source)
        return CompiledAgentGraph(
            nodes=dict(self._nodes),
            order=list(self._order),
            edges={key: list(value) for key, value in self._edges.items()},
            conditional=dict(self._conditional),
            entry=self._entry,
            checkpointer=checkpointer,
            limits=limits or GraphLimits(),
        )


@dataclass
class CompiledAgentGraph(Generic[StateT]):
    nodes: Dict[str, _NodeSpec[StateT]]
    order: List[str]
    edges: Dict[str, List[str]]
    conditional: Dict[str, RouteFn[StateT]]
    entry: str
    limits: GraphLimits
    checkpointer: Optional[CheckpointStore] = None
    _node_order: Dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        self._node_order = {name: index for index, name in enumerate(self.order)}

    def topology_hash(self) -> str:
        """Stable digest of graph structure, never handler object identity."""
        payload = {
            "nodes": [
                {
                    "name": name,
                    "deadline_ms": self.nodes[name].deadline_ms,
                    "retries": self.nodes[name].retries,
                    "has_fallback": self.nodes[name].fallback is not None,
                }
                for name in self.order
            ],
            "edges": {
                source: list(targets) for source, targets in sorted(self.edges.items())
            },
            "conditional": {
                source: getattr(route, "__qualname__", getattr(route, "__name__", ""))
                for source, route in sorted(self.conditional.items())
            },
            "entry": self.entry,
            "limits": self.limits.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    async def invoke_turn(self, state: StateT, ctx: TurnContext) -> StateT:
        frontier = [self.entry]
        superstep = 1
        current = state
        while frontier:
            if superstep > self.limits.max_supersteps_per_turn:
                raise GraphCycleLimitExceeded(
                    "graph exceeded max_supersteps_per_turn=%d"
                    % self.limits.max_supersteps_per_turn
                )
            snapshot = current
            frontier = self._dedupe(frontier)
            executor_nodes = [
                self._graph_node(self.nodes[name], snapshot, ctx)
                for name in sorted(frontier, key=self._node_order.__getitem__)
            ]
            try:
                await GraphExecutor(executor_nodes).run({}, context=ctx)
            except asyncio.CancelledError:
                raise

            for name in sorted(frontier, key=self._node_order.__getitem__):
                update = ctx.results.get(name)
                if not isinstance(update, dict):
                    raise GraphExecutionError(
                        "node '%s' returned non-dict state update" % name
                    )
                current = current.merged(update)  # type: ignore[assignment]

            await self._save_checkpoint(current, ctx, superstep, "superstep")
            frontier = self._next_frontier(frontier, current)
            superstep += 1
        return current

    def _graph_node(
        self,
        spec: _NodeSpec[StateT],
        snapshot: StateT,
        ctx: TurnContext,
    ) -> GraphNode:
        async def handler(context: GraphContext) -> Dict[str, Any]:
            cast(TurnContext, context)
            return await spec.handler(snapshot, ctx)

        async def fallback(context: GraphContext) -> Dict[str, Any]:
            cast(TurnContext, context)
            assert spec.fallback is not None
            return await spec.fallback(snapshot, ctx)

        return GraphNode(
            name=spec.name,
            handler=handler,
            deadline_ms=spec.deadline_ms,
            retries=spec.retries,
            fallback=fallback if spec.fallback is not None else None,
        )

    def _next_frontier(self, frontier: List[str], state: StateT) -> List[str]:
        targets: List[str] = []
        for source in frontier:
            for target in self.edges.get(source, []):
                targets.append(target)
            route = self.conditional.get(source)
            if route is not None:
                targets.append(route(state))
        known: List[str] = []
        for target in targets:
            if target == END:
                continue
            if target not in self.nodes:
                raise GraphRouteError("route returned unknown node: %s" % target)
            if target not in known:
                known.append(target)
        return known

    async def _save_checkpoint(
        self,
        state: StateT,
        ctx: TurnContext,
        superstep: int,
        kind: str,
    ) -> None:
        if self.checkpointer is None:
            return
        await self.checkpointer.save(
            Checkpoint(
                checkpoint_id=checkpoint_id(ctx.session_id, ctx.turn_id, superstep),
                session_id=ctx.session_id,
                thread_id=ctx.thread_id or ctx.session_id,
                turn_id=ctx.turn_id,
                superstep=superstep,
                kind=kind,  # type: ignore[arg-type]
                state=state,
                created_at_ms=int(ctx.clock.monotonic() * 1000),
            )
        )

    def _dedupe(self, items: List[str]) -> List[str]:
        result: List[str] = []
        for item in items:
            if item not in result:
                result.append(item)
        return result


def _history_from_state(state: ConversationState) -> List[LlmMessage]:
    messages: List[LlmMessage] = []
    for line in state.transcript:
        role = "user" if line.speaker == "caller" else "assistant"
        messages.append(LlmMessage(role=role, content=line.text))
    return messages


def _empty_context_update(state: ConversationState) -> Dict[str, Any]:
    agent_state = {
        **state.agent_state,
        "prompt_context": "",
        "grounding_ids": [],
        "rag_deadline_exceeded": False,
        "current_rag_requests": 0,
    }
    return {"agent_state": agent_state}


RAG_DISPATCH_COUNT_PAYLOAD_KEY = "_lucy_rag_dispatch_count"


def default_agent_graph(
    inner: TurnDriver,
    rag: Optional[SpeculativeRagNode] = None,
    classify: Optional[Callable[[ConversationState], Optional[FunnelStage]]] = None,
) -> AgentGraph[ConversationState]:
    graph: AgentGraph[ConversationState] = AgentGraph()

    async def context_synthesis(
        state: ConversationState, ctx: TurnContext
    ) -> Dict[str, Any]:
        user_text = str(ctx.payload.get("user_text", ""))
        if rag is None:
            return _empty_context_update(state)
        dispatched = not rag.is_cached(user_text)
        ctx.payload[RAG_DISPATCH_COUNT_PAYLOAD_KEY] = int(dispatched)
        if dispatched and ctx.rag_dispatch_observer is not None:
            ctx.rag_dispatch_observer()
        result = await rag.prefetch(user_text)
        ctx.payload[RAG_RESULT_PAYLOAD_KEY] = result
        agent_state = {
            **state.agent_state,
            "prompt_context": result.prompt_context,
            "grounding_ids": [chunk.grounding_id for chunk in result.chunks],
            "rag_deadline_exceeded": result.deadline_exceeded,
            "current_rag_requests": int(dispatched),
        }
        return {"agent_state": agent_state}

    async def context_fallback(
        state: ConversationState, ctx: TurnContext
    ) -> Dict[str, Any]:
        ctx.payload.pop(RAG_RESULT_PAYLOAD_KEY, None)
        update = _empty_context_update(state)
        update["agent_state"]["current_rag_requests"] = int(
            ctx.payload.get(RAG_DISPATCH_COUNT_PAYLOAD_KEY, 0)
        )
        return update

    async def llm(state: ConversationState, ctx: TurnContext) -> Dict[str, Any]:
        user_text = str(ctx.payload.get("user_text", ""))
        assistant_text = ""
        terminal = TurnDriverReport(
            assistant_text="",
            llm_ms=0.0,
            usage=None,
        )
        history = _history_from_state(state)
        if ctx.current_user_in_state and history and history[-1].role == "user":
            history.pop()
        rag_result = ctx.payload.get(RAG_RESULT_PAYLOAD_KEY)
        if isinstance(rag_result, RagResult):
            context_message = grounded_context_message(rag_result)
            if context_message is not None:
                history.insert(0, context_message)
        async for event in inner.run_turn(
            user_text,
            history,
            turn_context=ctx,
        ):
            if isinstance(event, TtsSpeak):
                if ctx.emit is not None:
                    ctx.emit(event)
            elif isinstance(event, TurnDriverReport):
                terminal = event
                assistant_text = event.assistant_text
        transcript = [
            *state.transcript,
            TranscriptLine(speaker="agent", text=assistant_text),
        ]
        agent_state = {
            **state.agent_state,
            "last_turn_report": {
                "assistant_text": terminal.assistant_text,
                "llm_ms": terminal.llm_ms,
                "mcp_tools_ms": terminal.mcp_tools_ms,
                "prompt_tokens": terminal.usage.prompt_tokens if terminal.usage else 0,
                "completion_tokens": (
                    terminal.usage.completion_tokens if terminal.usage else 0
                ),
                "cached_prompt_tokens": (
                    terminal.usage.cached_prompt_tokens if terminal.usage else 0
                ),
                "mcp_tool_calls": terminal.mcp_tool_calls,
                "rag_requests": terminal.rag_requests
                + int(state.agent_state.get("current_rag_requests", 0)),
            },
        }
        return {"transcript": transcript, "agent_state": agent_state}

    async def finalize_funnel(
        state: ConversationState, ctx: TurnContext
    ) -> Dict[str, Any]:
        stage = classify(state) if classify is not None else state.funnel_stage
        return {"turns": state.turns + 1, "funnel_stage": stage}

    return (
        graph.add_node(
            "context_synthesis",
            context_synthesis,
            fallback=context_fallback,
        )
        .add_node("llm", llm)
        .add_node("finalize_funnel", finalize_funnel)
        .add_edge("context_synthesis", "llm")
        .add_edge("llm", "finalize_funnel")
        .set_entry("context_synthesis")
    )
