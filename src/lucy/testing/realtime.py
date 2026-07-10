"""Deterministic in-process realtime provider and session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional, Sequence

from lucy.clock import Clock
from lucy.drivers import (
    RealtimeAssistantDelta,
    RealtimeAssistantDone,
    RealtimeEvent,
    RealtimeSessionConfig,
    RealtimeUserTranscript,
)
from lucy.llm import ToolCallReady, UsageReport
from lucy.tools import ToolResult


@dataclass(frozen=True)
class ScriptedRealtimeToolCall:
    call_id: str
    name: str
    arguments: dict
    followup_text: str


@dataclass
class ScriptedRealtimeTurn:
    user_text: str
    assistant_text: str
    usage: UsageReport
    tool_calls: Sequence[ScriptedRealtimeToolCall] = ()


class LocalRealtimeSession:
    def __init__(
        self, turns: Sequence[ScriptedRealtimeTurn], clock: Clock, interval_ms: float
    ) -> None:
        self._turns = list(turns)
        self._clock = clock
        self._interval_s = interval_ms / 1000.0
        self._index = 0
        self._tool_result_ready = asyncio.Event()
        self.received_tool_results: List[ToolResult] = []
        self.interrupted = False
        self.closed = False
        self._voiced = ""

    async def _pace(self) -> None:
        await self._clock.sleep(self._interval_s)

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        turn = self._turns[self._index]
        self._index += 1
        utterance_id = "realtime-%d" % self._index
        self.interrupted = False
        self._voiced = ""

        words = turn.user_text.split()
        for index in range(len(words)):
            await self._pace()
            if self.interrupted:
                yield RealtimeAssistantDone(utterance_id, self._voiced)
                return
            yield RealtimeUserTranscript(
                text=" ".join(words[: index + 1]), final=index == len(words) - 1
            )

        for chunk in _text_chunks(turn.assistant_text):
            await self._pace()
            if self.interrupted:
                yield RealtimeAssistantDone(utterance_id, self._voiced)
                return
            self._voiced += chunk
            yield RealtimeAssistantDelta(utterance_id, chunk)

        for call in turn.tool_calls:
            self._tool_result_ready.clear()
            yield ToolCallReady(call.call_id, call.name, dict(call.arguments))
            await self._tool_result_ready.wait()
            for chunk in _text_chunks(call.followup_text):
                await self._pace()
                if self.interrupted:
                    yield RealtimeAssistantDone(utterance_id, self._voiced)
                    return
                self._voiced += chunk
                yield RealtimeAssistantDelta(utterance_id, chunk)

        if not self.interrupted:
            yield turn.usage
        yield RealtimeAssistantDone(utterance_id, self._voiced)

    async def send_tool_result(self, result: ToolResult) -> None:
        self.received_tool_results.append(result)
        self._tool_result_ready.set()

    async def interrupt(self) -> None:
        self.interrupted = True
        self._tool_result_ready.set()

    async def close(self) -> None:
        self.closed = True


class LocalRealtimeSimulator:
    def __init__(
        self,
        turns: List[ScriptedRealtimeTurn],
        clock: Clock,
        transcript_interval_ms: float,
    ) -> None:
        self.turns = list(turns)
        self.clock = clock
        self.transcript_interval_ms = transcript_interval_ms
        self.session_interval_override: Optional[float] = None
        self.opened_configs: List[RealtimeSessionConfig] = []
        self.session: LocalRealtimeSession

    async def open(self, config: RealtimeSessionConfig) -> LocalRealtimeSession:
        self.opened_configs.append(config)
        interval = (
            self.session_interval_override
            if self.session_interval_override is not None
            else self.transcript_interval_ms
        )
        self.session = LocalRealtimeSession(self.turns, self.clock, interval)
        return self.session


def _text_chunks(text: str) -> List[str]:
    words = text.split(" ")
    return [
        word + (" " if index < len(words) - 1 else "")
        for index, word in enumerate(words)
        if word
    ]
