"""Reusable no-mocks contract suites for provider plugin packages."""

from __future__ import annotations

import asyncio
from typing import List, Tuple

import pytest

from lucy.llm import (
    LlmProvider,
    LlmRequest,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
)
from lucy.voice import (
    AudioChunk,
    ProviderPayloadError,
    SttProvider,
    TtsProvider,
)

CONTRACT_TIMEOUT_SECONDS = 0.05


async def _assert_no_orphan_tasks(before: set[asyncio.Task]) -> None:
    await asyncio.sleep(0)
    assert asyncio.all_tasks() == before


class SttContractSuite:
    def make_provider(self) -> SttProvider:
        raise NotImplementedError

    def chunks(self) -> List[AudioChunk]:
        raise NotImplementedError

    def make_stalled_provider(self) -> SttProvider:
        raise NotImplementedError

    def make_malformed_case(self) -> Tuple[SttProvider, List[AudioChunk]]:
        raise NotImplementedError

    async def test_streaming_partials_accumulate_to_final(self):
        events = await self.make_provider().transcribe(self.chunks())

        finals = [event for event in events if event.is_final]
        assert events
        assert len(finals) == 1
        assert events[-1] is finals[0]
        assert finals[0].text
        assert [event.sequence for event in events] == sorted(
            event.sequence for event in events
        )

    async def test_stalled_transcription_times_out_cleanly(self):
        before = asyncio.all_tasks()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.make_stalled_provider().transcribe(self.chunks()),
                CONTRACT_TIMEOUT_SECONDS,
            )
        await _assert_no_orphan_tasks(before)

    async def test_cancellation_leaves_no_orphan_tasks(self):
        before = asyncio.all_tasks()
        task = asyncio.create_task(
            self.make_stalled_provider().transcribe(self.chunks())
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_no_orphan_tasks(before)

    async def test_malformed_payload_raises_provider_payload_error(self):
        provider, chunks = self.make_malformed_case()
        with pytest.raises(ProviderPayloadError):
            await provider.transcribe(chunks)


class TtsContractSuite:
    def make_provider(self) -> TtsProvider:
        raise NotImplementedError

    def text(self) -> str:
        raise NotImplementedError

    def make_stalled_provider(self) -> TtsProvider:
        raise NotImplementedError

    def make_malformed_case(self) -> Tuple[TtsProvider, str]:
        raise NotImplementedError

    async def test_stream_orders_started_chunks_finished(self):
        session_id = "contract-tts"
        events = await self.make_provider().synthesize(session_id, self.text())

        assert events[0].status == "started"
        assert events[-1].status == "finished"
        assert any(event.status == "chunk" for event in events[1:-1])
        assert {event.session_id for event in events} == {session_id}

    async def test_stalled_synthesis_times_out_cleanly(self):
        before = asyncio.all_tasks()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.make_stalled_provider().synthesize("contract-tts", self.text()),
                CONTRACT_TIMEOUT_SECONDS,
            )
        await _assert_no_orphan_tasks(before)

    async def test_cancellation_leaves_no_orphan_tasks(self):
        before = asyncio.all_tasks()
        task = asyncio.create_task(
            self.make_stalled_provider().synthesize("contract-tts", self.text())
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_no_orphan_tasks(before)

    async def test_malformed_payload_raises_provider_payload_error(self):
        provider, text = self.make_malformed_case()
        with pytest.raises(ProviderPayloadError):
            await provider.synthesize("contract-tts", text)


class LlmContractSuite:
    def make_provider(self) -> LlmProvider:
        raise NotImplementedError

    def request(self) -> LlmRequest:
        raise NotImplementedError

    def make_stalled_provider(self) -> LlmProvider:
        raise NotImplementedError

    def make_tool_call_case(self) -> Tuple[LlmProvider, LlmRequest]:
        raise NotImplementedError

    def make_malformed_case(self) -> Tuple[LlmProvider, LlmRequest]:
        raise NotImplementedError

    async def test_token_deltas_end_with_single_stream_end(self):
        events = [
            event async for event in self.make_provider().stream_chat(self.request())
        ]

        assert any(isinstance(event, TokenDelta) for event in events)
        endings = [event for event in events if isinstance(event, StreamEnd)]
        assert len(endings) == 1
        assert events[-1] is endings[0]

    async def test_usage_report_emitted_before_stream_end(self):
        events = [
            event async for event in self.make_provider().stream_chat(self.request())
        ]
        usage_indexes = [
            index
            for index, event in enumerate(events)
            if isinstance(event, UsageReport)
        ]
        end_index = next(
            index for index, event in enumerate(events) if isinstance(event, StreamEnd)
        )

        assert len(usage_indexes) == 1
        assert usage_indexes[0] < end_index

    async def test_tool_call_deltas_assemble_to_tool_call_ready(self):
        provider, request = self.make_tool_call_case()
        events = [event async for event in provider.stream_chat(request)]
        deltas = [event for event in events if isinstance(event, ToolCallDelta)]
        ready = [event for event in events if isinstance(event, ToolCallReady)]

        assert deltas
        assert len(ready) == 1
        assert events.index(deltas[0]) < events.index(ready[0])
        assert ready[0].arguments

    async def test_cancellation_mid_stream_closes_generator(self):
        provider = self.make_stalled_provider()
        before = asyncio.all_tasks()

        async def consume() -> None:
            async for _ in provider.stream_chat(self.request()):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_no_orphan_tasks(before)

    async def test_malformed_wire_frame_raises_provider_payload_error(self):
        provider, request = self.make_malformed_case()
        try:
            events = [event async for event in provider.stream_chat(request)]
        except ProviderPayloadError:
            return

        assert isinstance(events[-1], StreamEnd)
        assert events[-1].finish_reason == "error"
