from lucy.clock import ManualClock
from lucy.llm import (
    LlmMessage,
    LlmRequest,
    LocalLlmSimulator,
    ScriptedLlmTurn,
    ToolCallReady,
    UsageReport,
)
from lucy.testing import LocalSttSimulator, LocalTtsSimulator
from lucy.testing.contracts import LlmContractSuite, SttContractSuite, TtsContractSuite
from lucy.voice import AudioChunk


class TestLocalSttSimulatorContract(SttContractSuite):
    def make_provider(self):
        return LocalSttSimulator()

    def chunks(self):
        return [AudioChunk(session_id="stt", data=b"hello", sequence=0)]

    def make_stalled_provider(self):
        return LocalSttSimulator(delay_ms=60_000)

    def make_malformed_case(self):
        return (
            LocalSttSimulator(),
            [AudioChunk(session_id="stt", data=b"\xff", sequence=0)],
        )


class TestLocalTtsSimulatorContract(TtsContractSuite):
    def make_provider(self):
        return LocalTtsSimulator()

    def text(self):
        return "Hello from Lucy"

    def make_stalled_provider(self):
        return LocalTtsSimulator(delay_ms=60_000)

    def make_malformed_case(self):
        return (LocalTtsSimulator(), "   ")


def _request() -> LlmRequest:
    return LlmRequest(
        provider="local",
        model="simulator",
        messages=[LlmMessage(role="user", content="Hello")],
    )


class TestLocalLlmSimulatorContract(LlmContractSuite):
    def make_provider(self):
        return LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["Hello"], usage=UsageReport(1, 1))],
            ManualClock(),
            token_interval_ms=0,
        )

    def request(self):
        return _request()

    def make_stalled_provider(self):
        return LocalLlmSimulator(
            [ScriptedLlmTurn(tokens=["late"], usage=UsageReport(1, 1))],
            ManualClock(),
            token_interval_ms=60_000,
        )

    def make_tool_call_case(self):
        provider = LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=[],
                    usage=UsageReport(2, 1),
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallReady(
                            call_id="call-1",
                            name="lookup",
                            arguments={"query": "Lucy"},
                        )
                    ],
                )
            ],
            ManualClock(),
            token_interval_ms=0,
        )
        return (provider, _request())

    def make_malformed_case(self):
        provider = LocalLlmSimulator(
            [
                ScriptedLlmTurn(
                    tokens=[],
                    usage=UsageReport(0, 0),
                    finish_reason="error",
                )
            ],
            ManualClock(),
            token_interval_ms=0,
        )
        return (provider, _request())
