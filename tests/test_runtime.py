import asyncio
import time

import pytest

from lucy.runtime import GraphContext, GraphExecutionError, GraphExecutor, GraphNode


async def first_node(context: GraphContext):
    return context.payload["input"].upper()


async def second_node(context: GraphContext):
    return context.results["first"] + "!"


def test_graph_executor_runs_nodes_in_order():
    executor = GraphExecutor(
        nodes=[
            GraphNode(name="first", handler=first_node),
            GraphNode(name="second", handler=second_node),
        ]
    )

    context = asyncio.run(executor.run({"input": "hello"}))

    assert context.results["second"] == "HELLO!"
    assert [event.node for event in context.trace] == ["first", "second"]


def test_graph_executor_uses_fallback_after_timeout():
    async def slow_node(context: GraphContext):
        await asyncio.sleep(0.02)
        return "slow"

    async def fallback(context: GraphContext):
        return "fallback"

    executor = GraphExecutor(
        nodes=[
            GraphNode(
                name="stt",
                handler=slow_node,
                deadline_ms=1,
                fallback=fallback,
            )
        ]
    )

    context = asyncio.run(executor.run({}))

    assert context.results["stt"] == "fallback"
    assert context.trace[0].status == "fallback"


def test_graph_executor_raises_without_fallback():
    async def failing_node(context: GraphContext):
        raise ValueError("provider failed")

    executor = GraphExecutor(nodes=[GraphNode(name="llm", handler=failing_node)])

    with pytest.raises(GraphExecutionError):
        asyncio.run(executor.run({}))


def test_graph_executor_retries_before_success():
    attempts = {"count": 0}

    async def flaky_node(context: GraphContext):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("transient provider failure")
        return "ok"

    executor = GraphExecutor(
        nodes=[GraphNode(name="llm", handler=flaky_node, retries=1)]
    )

    context = asyncio.run(executor.run({}))

    assert attempts["count"] == 2
    assert context.results["llm"] == "ok"
    assert context.trace[0].status == "ok"


def test_graph_executor_propagates_cancellation():
    async def cancelled_node(context: GraphContext):
        raise asyncio.CancelledError()

    executor = GraphExecutor(nodes=[GraphNode(name="tts", handler=cancelled_node)])

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(executor.run({}))


def test_dag_executor_runs_independent_nodes_concurrently():
    async def branch_a(context: GraphContext):
        await asyncio.sleep(0.03)
        return "a"

    async def branch_b(context: GraphContext):
        await asyncio.sleep(0.03)
        return "b"

    async def join(context: GraphContext):
        return context.results["branch_a"] + context.results["branch_b"]

    executor = GraphExecutor(
        nodes=[
            GraphNode(name="branch_a", handler=branch_a),
            GraphNode(name="branch_b", handler=branch_b),
            GraphNode(
                name="join",
                handler=join,
                depends_on=["branch_a", "branch_b"],
            ),
        ]
    )

    started = time.perf_counter()
    context = asyncio.run(executor.run({}))
    elapsed = time.perf_counter() - started

    assert context.results["join"] == "ab"
    assert elapsed < 0.055
    assert [event.node for event in context.trace] == ["branch_a", "branch_b", "join"]


def test_dag_executor_cancels_active_branches_on_failure():
    cancelled = {"slow_branch": False}

    async def failing_branch(context: GraphContext):
        await asyncio.sleep(0.005)
        raise ValueError("branch failed")

    async def slow_branch(context: GraphContext):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled["slow_branch"] = True
            raise

    executor = GraphExecutor(
        nodes=[
            GraphNode(name="failing_branch", handler=failing_branch),
            GraphNode(name="slow_branch", handler=slow_branch),
            GraphNode(
                name="join",
                handler=second_node,
                depends_on=["failing_branch", "slow_branch"],
            ),
        ]
    )

    with pytest.raises(GraphExecutionError):
        asyncio.run(executor.run({}))

    assert cancelled["slow_branch"] is True


def test_dag_executor_rejects_unknown_dependency():
    executor = GraphExecutor(
        nodes=[
            GraphNode(name="join", handler=second_node, depends_on=["missing"]),
        ]
    )

    with pytest.raises(GraphExecutionError, match="unknown dependency"):
        asyncio.run(executor.run({}))
