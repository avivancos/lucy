# Agent graphs

Lucy agents run on an `AgentGraph`: a stateful per-turn cognition graph
over the async executor. Simple agents can keep the default; custom
agents author nodes and edges explicitly.

## The default graph

`default_agent_graph` wires three nodes:

`context_synthesis` -> `llm` -> `finalize_funnel`

Simple agents author nothing. The facade `VoiceAgent` uses a minimal
single-node responder for the quickstart; production turn drivers use
the default three-node shape (or a prebuilt graph below).

## Authoring an `AgentGraph`

Builder API (as landed):

- `add_node(name, handler, *, deadline_ms, retries, fallback)`
- `add_edge(source, target)`
- `add_conditional_edge(source, route_fn)`
- `set_entry(name)`
- `compile(checkpointer, limits)`
- `END` sentinel for terminal edges

Handlers receive `(state, TurnContext)` and return a state-update dict.
Invoke a compiled graph with `invoke_turn(state, TurnContext(...))`.

```python
import asyncio

from lucy.graph import END, AgentGraph
from lucy.runtime import TurnContext
from lucy.state import ConversationState

async def choose(state: ConversationState, ctx: TurnContext):
    return {"slots": {"route": "book"}}

async def book(state: ConversationState, ctx: TurnContext):
    print("routed: book")
    return {"agent_state": {"path": "book"}}

async def other(state: ConversationState, ctx: TurnContext):
    print("routed: other")
    return {"agent_state": {"path": "other"}}

async def main():
    state = await (
        AgentGraph[ConversationState]()
        .add_node("choose", choose)
        .add_node("book", book)
        .add_node("other", other)
        .add_conditional_edge("choose", lambda s: s.slots["route"])
        .add_edge("book", END)
        .add_edge("other", END)
        .set_entry("choose")
        .compile()
        .invoke_turn(ConversationState(), TurnContext(payload={}))
    )
    print("final:", state.agent_state["path"])

asyncio.run(main())
```

## State, checkpoints, resume

`ConversationState.merged` applies a partial update and rejects unknown
keys. Each superstep can write a `Checkpoint` through an
`InMemoryCheckpointStore` (or a durable store). After a process kill,
`GraphTurnDriver.resume` reloads the latest checkpoint for a thread.
Post-call, `ConversationHarness.replay` compares recorded events to
checkpoint history.

## Prebuilt nodes

From `lucy.nodes`, by family:

**Perception / context**

- `ContextSynthesisNode` — speculative RAG context for the turn
- `SlotFillerNode` — fill typed slots from caller text
- `SentimentNode` — label caller sentiment
- `FunnelClassifierNode` — map the turn onto a funnel stage
- `LanguageDetectNode` — detect caller language

**Decision / control**

- `IntentRouterNode` — route by classified intent
- `GuardrailNode` — policy gate before speech/tools
- `DisclosureNode` — required disclosure before continuing

**Action / speech**

- `LlmNode` — streamed LLM generation paced by budgets
- `McpToolNode` — MCP tool call with permission checks
- `SayNode` — speak a fixed template
- `HandoffNode` — escalate to a human

**Telephony**

- `TransferNode`, `DtmfMenuNode`, `VoicemailDetectNode`, `DialNode`,
  `HoldNode`, `EndCallNode`

**Post-call**

- `SummaryNode`, `CrmSyncNode`, `DispositionNode`, `EvalHookNode`

## Prebuilt graphs

`lucy.prebuilt` ships four compiled applications:

- `booking_agent()` — six golden sales-booking scenarios
- `lead_qualifier()` — qualify and route a lead
- `receptionist()` — greet, route, transfer
- `survey_agent()` — multi-step survey then post-call summary

Wire a compiled graph through `GraphTurnDriver` and run it with
`ConversationHarness` against a scenario such as `booking_happy_path()`
(see [Transports and telephony](./transports-and-telephony.md) for the
offline harness shape).
