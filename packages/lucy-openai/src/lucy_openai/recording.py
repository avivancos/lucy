"""Real OpenAI fixture recording scenarios."""

from __future__ import annotations

from lucy.llm import LlmMessage, LlmRequest
from lucy.drivers import RealtimeSessionConfig
from lucy.testing.record import RecordingResult
from lucy.tools import ToolResult

from lucy_openai.llm import OpenAiLlmAdapter, OpenAiSseTransport
from lucy_openai.realtime import (
    OpenAiRealtimeAdapter,
    OpenAiRealtimeWebSocketTransport,
)
from lucy_openai.settings import OpenAiSettings

MODEL = "gpt-5-mini"
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_weather",
        "description": "Look up the weather in a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}


async def _record(request: LlmRequest) -> RecordingResult:
    settings = OpenAiSettings()
    transport = OpenAiSseTransport(settings)
    adapter = OpenAiLlmAdapter(MODEL, settings=settings, transport=transport)
    try:
        async for _ in adapter.stream_chat(request):
            pass
    finally:
        await transport.close()
    return RecordingResult(frames=list(transport.frames), artifacts={})


async def text_stream() -> RecordingResult:
    return await _record(
        LlmRequest(
            provider="openai",
            model=MODEL,
            messages=[
                LlmMessage(role="user", content="Reply with exactly: hello Lucy")
            ],
            max_output_tokens=256,
        )
    )


async def tool_call_stream() -> RecordingResult:
    return await _record(
        LlmRequest(
            provider="openai",
            model=MODEL,
            messages=[
                LlmMessage(
                    role="user",
                    content="Call lookup_weather for Madrid. Do not answer in text.",
                )
            ],
            tools=[WEATHER_TOOL],
            max_output_tokens=1_024,
        )
    )


SCENARIOS = {
    "text_stream": text_stream,
    "tool_call_stream": tool_call_stream,
}


async def realtime_text_turn() -> RecordingResult:
    settings = OpenAiSettings()
    transport = OpenAiRealtimeWebSocketTransport(settings, "gpt-realtime")
    adapter = OpenAiRealtimeAdapter(
        "gpt-realtime", settings=settings, transport=transport
    )
    session = await adapter.open(
        RealtimeSessionConfig(
            provider="openai",
            model="gpt-realtime",
            system_prompt="Reply briefly and clearly.",
        )
    )
    try:
        await session.send_text("Reply with exactly: realtime Lucy")
        async for _ in session.events():
            pass
        await session.send_tool_result(
            ToolResult("recorded-call", True, value={"temperature_c": 18})
        )
        await session.interrupt()
    finally:
        await transport.close()
    return RecordingResult(frames=list(transport.frames), artifacts={})


SCENARIOS["text_turn"] = realtime_text_turn
