import asyncio
import json
from typing import get_args

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from lucy.clock import ManualClock
from lucy.llm import (
    LlmMessage,
    LlmModelNotRegistered,
    LlmRequest,
    LlmStreamEvent,
    LocalLlmSimulator,
    OpenAiCompatibleAdapter,
    ScriptedLlmTurn,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
    resolve_llm,
)
from lucy.providers import default_model_registry


def _req():
    return LlmRequest(
        provider="openai",
        model="gpt-5",
        messages=[LlmMessage(role="user", content="hello")],
    )


async def _drain(clock: ManualClock, task, step_ms: float, max_steps: int = 60):
    """Advance virtual time until the consuming task completes (no wall time)."""
    for _ in range(max_steps):
        if task.done():
            break
        await asyncio.sleep(0)
        clock.advance(step_ms)
        await asyncio.sleep(0)
    await task


# -- C1: contract, request, resolution ---------------------------------------


def test_stream_event_union_covers_five_event_types():
    members = set(get_args(LlmStreamEvent))
    assert members == {TokenDelta, ToolCallDelta, ToolCallReady, UsageReport, StreamEnd}


def test_stream_events_are_frozen():
    with pytest.raises(Exception):
        TokenDelta(text="hi").text = "no"  # type: ignore[misc]


def test_llm_request_carries_cache_key_hint():
    req = LlmRequest(
        provider="openai",
        model="gpt-5",
        messages=[LlmMessage(role="user", content="hello")],
        cache_key="turn-1-prefix",
    )
    assert req.cache_key == "turn-1-prefix"
    assert LlmRequest.model_validate(req.model_dump()).cache_key == "turn-1-prefix"


def test_llm_request_requires_at_least_one_message():
    with pytest.raises(ValidationError):
        LlmRequest(provider="openai", model="gpt-5", messages=[])


def test_resolve_llm_accepts_a_registered_llm_model():
    info = resolve_llm(default_model_registry(), "openai", "gpt-5")
    from lucy.providers import Capability

    assert Capability.LLM in info.capabilities


def test_resolve_llm_rejects_model_without_llm_capability():
    # gpt-4o-transcribe is an STT-only entry in the default registry
    with pytest.raises(LlmModelNotRegistered):
        resolve_llm(default_model_registry(), "openai", "gpt-4o-transcribe")


def test_resolve_llm_rejects_unregistered_pair():
    with pytest.raises(LlmModelNotRegistered):
        resolve_llm(default_model_registry(), "nobody", "no-such-model")


# -- C2: LocalLlmSimulator ---------------------------------------------------


async def test_simulator_streams_tokens_then_usage_then_end():
    clock = ManualClock()
    usage = UsageReport(prompt_tokens=5, completion_tokens=3)
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["Hel", "lo", "!"], usage=usage)],
        clock,
        token_interval_ms=10,
    )
    events: list = []

    async def consume():
        async for ev in sim.stream_chat(_req()):
            events.append(ev)

    task = asyncio.create_task(consume())
    await _drain(clock, task, 10)

    assert [type(e).__name__ for e in events] == [
        "TokenDelta",
        "TokenDelta",
        "TokenDelta",
        "UsageReport",
        "StreamEnd",
    ]
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["Hel", "lo", "!"]
    assert events[-2] == usage
    assert events[-1] == StreamEnd(finish_reason="stop")


async def test_simulator_paces_tokens_via_manual_clock_without_wall_time():
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["a", "b", "c", "d"], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=25,
    )

    async def consume():
        async for _ in sim.stream_chat(_req()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(_drain(clock, task, 25), timeout=2.0)
    # four tokens each paced 25 ms of VIRTUAL time
    assert clock.monotonic() == pytest.approx(4 * 0.025)


async def test_simulator_cancellation_mid_stream_sets_cancelled():
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [ScriptedLlmTurn(tokens=["a", "b", "c"], usage=UsageReport(1, 1))],
        clock,
        token_interval_ms=10,
    )

    async def consume():
        async for _ in sim.stream_chat(_req()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    clock.advance(10)  # emit the first token
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sim.cancelled is True


async def test_simulator_consumes_a_scripted_turn_per_call():
    clock = ManualClock()
    sim = LocalLlmSimulator(
        [
            ScriptedLlmTurn(tokens=["one"], usage=UsageReport(1, 1)),
            ScriptedLlmTurn(tokens=["two"], usage=UsageReport(2, 2)),
        ],
        clock,
        token_interval_ms=5,
    )

    async def first_token():
        async for ev in sim.stream_chat(_req()):
            if isinstance(ev, TokenDelta):
                return ev.text
        return None

    t1 = asyncio.create_task(first_token())
    await _drain(clock, t1, 5)
    t2 = asyncio.create_task(first_token())
    await _drain(clock, t2, 5)
    assert t1.result() == "one" and t2.result() == "two"


# -- C3: OpenAI-compatible adapter vs a real in-process SSE server ------------


def _sse_app(chunks: list[dict]):
    """A local protocol server (ADR 0003) speaking the chat-completions SSE wire."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(_request: Request) -> StreamingResponse:
        async def body():
            for chunk in chunks:
                yield "data: %s\n\n" % json.dumps(chunk)
            yield "data: [DONE]\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


async def _collect(app, request=None):
    request = request or _req()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sse") as client:
        adapter = OpenAiCompatibleAdapter("http://sse/v1", http_client=client)
        return [event async for event in adapter.stream_chat(request)]


async def test_adapter_parses_sse_tokens_and_usage():
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
            ]
        )
    )
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["Hel", "lo"]
    usage = next(e for e in events if isinstance(e, UsageReport))
    assert usage.prompt_tokens == 5 and usage.completion_tokens == 2
    # usage precedes the terminal StreamEnd, matching the simulator's order
    assert events[-1] == StreamEnd(finish_reason="stop")
    assert events.index(usage) < len(events) - 1


async def test_adapter_assembles_tool_call_deltas_into_ready():
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "book_meeting", "arguments": ""}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"day\":"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"Tue\"}"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
        )
    )
    deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    assert deltas and all(d.call_id == "call_1" for d in deltas)
    ready = next(e for e in events if isinstance(e, ToolCallReady))
    assert ready.name == "book_meeting"
    assert ready.arguments == {"day": "Tue"}
    assert events[-1] == StreamEnd(finish_reason="tool_calls")


# -- card 64: fail-safe SSE + empty stream ------------------------------------


def _raw_sse_app(lines: list[str]):
    """SSE server emitting raw lines verbatim - can speak malformed frames."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(_request: Request) -> StreamingResponse:
        async def body():
            for line in lines:
                yield "data: %s\n\n" % line

        return StreamingResponse(body(), media_type="text/event-stream")

    return app


async def test_adapter_emits_error_finish_on_malformed_sse_line():
    events = await _collect(
        _raw_sse_app(
            [
                json.dumps({"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]}),
                "{not valid json",  # hostile/buggy upstream frame
                json.dumps({"choices": [{"delta": {"content": "lost"}, "finish_reason": "stop"}]}),
                "[DONE]",
            ]
        )
    )
    # tokens before the malformed line survive; nothing after it is emitted
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["Hi"]
    assert events[-1] == StreamEnd(finish_reason="error")


async def test_adapter_emits_error_finish_on_malformed_tool_arguments():
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "book", "arguments": "{broken"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
        )
    )
    assert not any(isinstance(e, ToolCallReady) for e in events)
    assert events[-1] == StreamEnd(finish_reason="error")


async def test_adapter_empty_stream_yields_clean_stop():
    events = await _collect(_sse_app([]))  # zero content chunks before [DONE]
    assert events == [StreamEnd(finish_reason="stop")]


async def test_adapter_emits_error_finish_on_malformed_usage_fields():
    # sec-001: non-numeric usage fields from a hostile/buggy endpoint must
    # terminate fail-safe, not raise ValueError out of stream_chat.
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
                {"choices": [], "usage": {"prompt_tokens": "NaN", "completion_tokens": 2}},
            ]
        )
    )
    assert [e.text for e in events if isinstance(e, TokenDelta)] == ["Hi"]
    assert events[-1] == StreamEnd(finish_reason="error")


async def test_adapter_emits_error_finish_on_malformed_tool_index():
    # sec-002: a non-integer tool_call index must not crash the turn.
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"tool_calls": [{"index": "oops", "id": "c1", "function": {"name": "book", "arguments": "{}"}}]}, "finish_reason": None}]},
            ]
        )
    )
    assert events[-1] == StreamEnd(finish_reason="error")


async def test_adapter_emits_error_finish_on_non_object_chunk():
    # valid JSON that is not an object (chunk.get would raise) is malformed
    events = await _collect(_raw_sse_app(["[1, 2, 3]", "[DONE]"]))
    assert events[-1] == StreamEnd(finish_reason="error")


async def test_adapter_emits_error_finish_on_non_object_tool_arguments():
    # tool arguments must decode to a dict; "[1, 2]" is upstream garbage
    events = await _collect(
        _sse_app(
            [
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "book", "arguments": "[1, 2]"}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
        )
    )
    assert not any(isinstance(e, ToolCallReady) for e in events)
    assert events[-1] == StreamEnd(finish_reason="error")
