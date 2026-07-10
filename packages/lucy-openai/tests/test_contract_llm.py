from pathlib import Path

import pytest

from lucy.llm import LlmMessage, LlmRequest
from lucy.testing.contracts import LlmContractSuite
from lucy.testing.replay import (
    RecordedFrame,
    ReplayTransport,
    StalledTransport,
    load_fixture,
)

from lucy_openai.llm import OpenAiLlmAdapter
from lucy_openai.settings import OpenAiSettings

FIXTURES = Path(__file__).parent / "fixtures"
TEXT_FIXTURE = FIXTURES / "llm_text_stream.jsonl"
TOOL_FIXTURE = FIXTURES / "llm_tool_call_stream.jsonl"
MODEL = "gpt-5-mini"
TOOL = {
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


def _request(*, tools=None) -> LlmRequest:
    content = (
        "Call lookup_weather for Madrid. Do not answer in text."
        if tools
        else "Reply with exactly: hello Lucy"
    )
    return LlmRequest(
        provider="openai",
        model=MODEL,
        messages=[LlmMessage(role="user", content=content)],
        tools=tools,
        max_output_tokens=1_024 if tools else 256,
    )


class TestOpenAiLlmContract(LlmContractSuite):
    def make_provider(self):
        return OpenAiLlmAdapter(
            MODEL,
            settings=OpenAiSettings(api_key="fixture-key"),
            transport=ReplayTransport(load_fixture(TEXT_FIXTURE)),
        )

    def request(self):
        return _request()

    def make_stalled_provider(self):
        return OpenAiLlmAdapter(
            MODEL,
            settings=OpenAiSettings(api_key="fixture-key"),
            transport=StalledTransport(),
        )

    def make_tool_call_case(self):
        return (
            OpenAiLlmAdapter(
                MODEL,
                settings=OpenAiSettings(api_key="fixture-key"),
                transport=ReplayTransport(load_fixture(TOOL_FIXTURE)),
            ),
            _request(tools=[TOOL]),
        )

    def make_malformed_case(self):
        frames = load_fixture(TEXT_FIXTURE)
        corrupted = list(frames)
        received_index = next(
            index
            for index, frame in enumerate(corrupted)
            if frame.direction == "received"
        )
        frame = corrupted[received_index]
        corrupted[received_index] = RecordedFrame(
            direction=frame.direction,
            at_ms=frame.at_ms,
            payload={"data": {"choices": "not-a-list"}},
        )
        return (
            OpenAiLlmAdapter(
                MODEL,
                settings=OpenAiSettings(api_key="fixture-key"),
                transport=ReplayTransport(corrupted),
            ),
            _request(),
        )


@pytest.mark.live
class TestOpenAiLlmLiveContract(LlmContractSuite):
    def make_provider(self):
        return OpenAiLlmAdapter(MODEL)

    def request(self):
        return _request()

    def make_stalled_provider(self):
        return OpenAiLlmAdapter(
            MODEL,
            settings=OpenAiSettings(api_key="fixture-key"),
            transport=StalledTransport(),
        )

    def make_tool_call_case(self):
        return OpenAiLlmAdapter(MODEL), _request(tools=[TOOL])

    def make_malformed_case(self):
        return TestOpenAiLlmContract().make_malformed_case()
