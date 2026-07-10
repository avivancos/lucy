import asyncio

import pytest
from pydantic import ValidationError

from lucy.nodes.base import NodeConfig, as_graph_node
from lucy.runtime import GraphExecutor, TurnContext
from lucy.state import ConversationState


class ExampleNode:
    name = "example"

    def __init__(self, config: NodeConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext):
        await asyncio.Event().wait()

    async def fallback(self, state: ConversationState, ctx: TurnContext):
        return {"agent_state": {"fallback": True}}


def test_node_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        NodeConfig(deadline_ms=10, unknown=True)


def test_as_graph_node_maps_deadline_retries_and_fallback():
    node = as_graph_node(ExampleNode(NodeConfig(deadline_ms=7, retries=2)))

    assert node.name == "example"
    assert node.deadline_ms == 7
    assert node.retries == 2
    assert node.fallback is not None


async def test_node_deadline_timeout_triggers_fallback_under_executor():
    node = as_graph_node(ExampleNode(NodeConfig(deadline_ms=1)))
    context = TurnContext(payload={"conversation_state": ConversationState()})

    result = await GraphExecutor([node]).run({}, context=context)

    assert result.results["example"] == {"agent_state": {"fallback": True}}
