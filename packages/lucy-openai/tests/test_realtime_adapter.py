from pathlib import Path

import pytest
from pydantic import SecretStr

from lucy.drivers import (
    RealtimeAssistantDelta,
    RealtimeAssistantDone,
    RealtimeSessionConfig,
)
from lucy.llm import UsageReport
from lucy.testing.replay import ReplayTransport, load_fixture
from lucy.tools import ToolResult

from lucy_openai import realtime_factory
from lucy_openai.realtime import (
    OpenAiRealtimeAdapter,
    OpenAiRealtimeSession,
)
from lucy_openai.settings import OpenAiSettings

FIXTURE = Path(__file__).parent / "fixtures" / "realtime_text_turn.jsonl"
MODEL = "gpt-realtime"
CONFIG = RealtimeSessionConfig(
    provider="openai",
    model=MODEL,
    system_prompt="Reply briefly and clearly.",
)
USER_TEXT = "Reply with exactly: realtime Lucy"
TOOL_CALL_ID = "recorded-call"
TOOL_RESULT = {"temperature_c": 18}


def _settings() -> OpenAiSettings:
    return OpenAiSettings(api_key=SecretStr("fixture-key"))


def _response_prefix():
    frames = load_fixture(FIXTURE)
    end = next(
        index
        for index, frame in enumerate(frames)
        if frame.direction == "received"
        and frame.payload.get("type") == "response.done"
    )
    return list(frames[: end + 1])


async def test_realtime_session_streams_transcript_deltas():
    adapter = OpenAiRealtimeAdapter(
        MODEL,
        settings=_settings(),
        transport=ReplayTransport(_response_prefix()),
    )
    session = await adapter.open(CONFIG)
    await session.send_text(USER_TEXT)
    events = [event async for event in session.events()]

    deltas = [event for event in events if isinstance(event, RealtimeAssistantDelta)]
    assert deltas
    assert "realtime Lucy" in "".join(event.text for event in deltas)
    assert any(isinstance(event, UsageReport) for event in events)
    assert isinstance(events[-1], RealtimeAssistantDone)


async def test_interrupt_sends_response_cancel():
    frame = next(
        frame
        for frame in load_fixture(FIXTURE)
        if frame.direction == "sent" and frame.payload == {"type": "response.cancel"}
    )
    session = OpenAiRealtimeSession(ReplayTransport([frame]))

    await session.interrupt()


async def test_send_tool_result_frames_match_recorded_shape():
    frames = load_fixture(FIXTURE)
    start = next(
        index
        for index, frame in enumerate(frames)
        if frame.direction == "sent"
        and frame.payload.get("item", {}).get("type") == "function_call_output"
    )
    session = OpenAiRealtimeSession(ReplayTransport(list(frames[start : start + 2])))

    await session.send_tool_result(ToolResult(TOOL_CALL_ID, True, value=TOOL_RESULT))


def test_realtime_factory_without_key_raises_named_error():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        realtime_factory(MODEL)
