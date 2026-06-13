"""Async multi-node runtime primitives."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set


NodeHandler = Callable[["GraphContext"], Awaitable[Any]]


@dataclass
class TraceEvent:
    node: str
    status: str
    started_at: float
    finished_at: float
    latency_ms: float
    error: Optional[str] = None


@dataclass
class GraphContext:
    payload: Dict[str, Any]
    results: Dict[str, Any] = field(default_factory=dict)
    trace: List[TraceEvent] = field(default_factory=list)


@dataclass
class GraphNode:
    name: str
    handler: NodeHandler
    deadline_ms: int = 500
    retries: int = 0
    fallback: Optional[NodeHandler] = None
    depends_on: List[str] = field(default_factory=list)


class GraphExecutionError(RuntimeError):
    """Raised when a graph node fails without a fallback."""


class GraphExecutor:
    """Small async graph executor for Lucy's first runtime milestone."""

    def __init__(self, nodes: List[GraphNode]):
        self.nodes = nodes

    async def run(self, payload: Dict[str, Any]) -> GraphContext:
        context = GraphContext(payload=payload)
        if any(node.depends_on for node in self.nodes):
            await self._run_dag(context)
            return context

        for node in self.nodes:
            await self._run_node(node, context)
        return context

    async def _run_dag(self, context: GraphContext) -> None:
        nodes_by_name = {node.name: node for node in self.nodes}
        if len(nodes_by_name) != len(self.nodes):
            raise GraphExecutionError("graph contains duplicate node names")

        known_names = set(nodes_by_name)
        for node in self.nodes:
            unknown = set(node.depends_on) - known_names
            if unknown:
                raise GraphExecutionError(
                    "node '%s' has unknown dependency: %s"
                    % (node.name, ", ".join(sorted(unknown)))
                )

        pending: Set[str] = set(nodes_by_name)
        active: Dict[asyncio.Task[None], GraphNode] = {}
        completed: Set[str] = set()

        while pending or active:
            ready = [
                name
                for name in pending
                if set(nodes_by_name[name].depends_on).issubset(completed)
            ]
            for name in sorted(ready, key=self._node_order):
                node = nodes_by_name[name]
                pending.remove(name)
                active[asyncio.create_task(self._run_node(node, context))] = node

            if not active:
                raise GraphExecutionError("graph has cyclic dependencies")

            done, _ = await asyncio.wait(
                active.keys(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in sorted(done, key=lambda item: self._node_order(active[item].name)):
                node = active.pop(task)
                try:
                    await task
                except asyncio.CancelledError:
                    await self._cancel_active(active)
                    raise
                except BaseException as exc:  # noqa: BLE001 - preserve graph error.
                    await self._cancel_active(active)
                    self._sort_trace(context)
                    if isinstance(exc, GraphExecutionError):
                        raise
                    raise GraphExecutionError(
                        "node '%s' failed: %s" % (node.name, exc)
                    ) from exc
                completed.add(node.name)

        self._sort_trace(context)

    async def _cancel_active(self, active: Dict[asyncio.Task[None], GraphNode]) -> None:
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active.keys(), return_exceptions=True)

    def _node_order(self, node_name: str) -> int:
        for index, node in enumerate(self.nodes):
            if node.name == node_name:
                return index
        return len(self.nodes)

    def _sort_trace(self, context: GraphContext) -> None:
        context.trace.sort(key=lambda event: self._node_order(event.node))

    async def _run_node(self, node: GraphNode, context: GraphContext) -> None:
        attempts = node.retries + 1
        last_error: Optional[BaseException] = None
        for _ in range(attempts):
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    node.handler(context),
                    timeout=node.deadline_ms / 1000,
                )
                finished = time.perf_counter()
                context.results[node.name] = result
                context.trace.append(
                    TraceEvent(
                        node=node.name,
                        status="ok",
                        started_at=started,
                        finished_at=finished,
                        latency_ms=(finished - started) * 1000,
                    )
                )
                return
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - captured for trace.
                last_error = exc

        if node.fallback is not None:
            started = time.perf_counter()
            result = await node.fallback(context)
            finished = time.perf_counter()
            context.results[node.name] = result
            context.trace.append(
                TraceEvent(
                    node=node.name,
                    status="fallback",
                    started_at=started,
                    finished_at=finished,
                    latency_ms=(finished - started) * 1000,
                    error=str(last_error) if last_error else None,
                )
            )
            return

        raise GraphExecutionError(
            "node '%s' failed: %s" % (node.name, last_error)
        ) from last_error
