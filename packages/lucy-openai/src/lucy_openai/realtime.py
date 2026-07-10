"""Control-plane adapter for the OpenAI Realtime WebSocket."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional, Union
from urllib.parse import urlencode

import websockets

from lucy.llm import (
    LlmStreamEvent,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
)
from lucy.testing.replay import RecordedFrame
from lucy.voice import ProviderPayloadError

from lucy_openai.settings import OpenAiSettings

MODEL_QUERY_FIELD = "model"
AUDIO_EVENT_TYPES = frozenset(
    {
        "response.output_audio.delta",
        "response.audio.delta",
    }
)
TRANSCRIPT_DELTA_TYPES = frozenset(
    {
        "response.output_text.delta",
        "response.output_audio_transcript.delta",
        "response.audio_transcript.delta",
    }
)


@dataclass(frozen=True)
class RealtimeTranscriptDelta:
    text: str


RealtimeControlEvent = Union[RealtimeTranscriptDelta, LlmStreamEvent]


class OpenAiRealtimeWebSocketTransport:
    def __init__(self, settings: OpenAiSettings, model: str) -> None:
        self.settings = settings
        self.model = model
        self.frames: List[RecordedFrame] = []
        self._started = time.monotonic()
        self._socket = None

    def _at_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1_000)

    async def _connect(self):
        if self._socket is not None:
            return self._socket
        if self.settings.api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required")
        url = "%s?%s" % (
            self.settings.realtime_url,
            urlencode({MODEL_QUERY_FIELD: self.model}),
        )
        self._socket = await websockets.connect(
            url,
            additional_headers={
                "Authorization": "Bearer %s" % self.settings.api_key.get_secret_value()
            },
        )
        return self._socket

    async def send(self, payload: dict) -> None:
        socket = await self._connect()
        await socket.send(json.dumps(payload))
        self.frames.append(RecordedFrame("sent", self._at_ms(), dict(payload)))

    async def receive(self) -> dict:
        socket = await self._connect()
        while True:
            try:
                payload = json.loads(await socket.recv())
            except (json.JSONDecodeError, TypeError) as exc:
                raise ProviderPayloadError("invalid OpenAI Realtime JSON") from exc
            if not isinstance(payload, dict):
                raise ProviderPayloadError("invalid OpenAI Realtime frame shape")
            event_type = payload.get("type")
            if not isinstance(event_type, str):
                raise ProviderPayloadError("OpenAI Realtime event type is required")
            if event_type in AUDIO_EVENT_TYPES:
                continue
            self.frames.append(RecordedFrame("received", self._at_ms(), dict(payload)))
            return payload

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None


class OpenAiRealtimeSession:
    def __init__(self, transport, *, owns_transport: bool = False) -> None:
        self.transport = transport
        self._owns_transport = owns_transport
        self._tool_states: Dict[str, Dict[str, str]] = {}

    async def send_text(self, text: str) -> None:
        if not text.strip():
            raise ProviderPayloadError("Realtime user text cannot be blank")
        await self.transport.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        await self.transport.send({"type": "response.create"})

    async def events(self) -> AsyncIterator[RealtimeControlEvent]:
        while True:
            payload = await self.transport.receive()
            event_type = payload.get("type")
            if event_type == "error":
                raise ProviderPayloadError(_error_message(payload))
            if event_type in TRANSCRIPT_DELTA_TYPES:
                delta = payload.get("delta")
                if not isinstance(delta, str):
                    raise ProviderPayloadError(
                        "OpenAI Realtime transcript delta must be a string"
                    )
                if delta:
                    yield RealtimeTranscriptDelta(text=delta)
                continue
            if event_type == "response.function_call_arguments.delta":
                yield self._tool_delta(payload)
                continue
            if event_type == "response.function_call_arguments.done":
                yield self._tool_ready(payload)
                continue
            if event_type == "response.done":
                response = _object(payload.get("response"), "Realtime response")
                usage = response.get("usage")
                if usage is not None:
                    yield _usage(_object(usage, "Realtime usage"))
                return

    async def send_tool_result(self, call_id: str, result: dict) -> None:
        if not call_id or not isinstance(result, dict):
            raise ProviderPayloadError("invalid Realtime tool result")
        await self.transport.send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result, sort_keys=True, separators=(",", ":")),
                },
            }
        )
        await self.transport.send({"type": "response.create"})

    async def interrupt(self) -> None:
        await self.transport.send({"type": "response.cancel"})

    async def close(self) -> None:
        if self._owns_transport and hasattr(self.transport, "close"):
            await self.transport.close()

    def _tool_delta(self, payload: dict) -> ToolCallDelta:
        call_id = payload.get("call_id")
        delta = payload.get("delta")
        if not isinstance(call_id, str) or not isinstance(delta, str):
            raise ProviderPayloadError("invalid Realtime tool-call delta")
        state = self._tool_states.setdefault(call_id, {"name": "", "arguments": ""})
        state["arguments"] += delta
        return ToolCallDelta(call_id=call_id, name=None, arguments_delta=delta)

    def _tool_ready(self, payload: dict) -> ToolCallReady:
        call_id = payload.get("call_id")
        name = payload.get("name")
        arguments = payload.get("arguments")
        if not isinstance(call_id, str) or not isinstance(name, str):
            raise ProviderPayloadError("invalid Realtime tool-call completion")
        if not isinstance(arguments, str):
            state = self._tool_states.get(call_id, {})
            arguments = state.get("arguments", "")
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderPayloadError("invalid Realtime tool arguments") from exc
        if not isinstance(parsed, dict):
            raise ProviderPayloadError("Realtime tool arguments must be an object")
        return ToolCallReady(call_id=call_id, name=name, arguments=parsed)


class OpenAiRealtimeAdapter:
    def __init__(
        self,
        model: str,
        settings: Optional[OpenAiSettings] = None,
        transport=None,
    ) -> None:
        self.model = model
        self.settings = settings or OpenAiSettings()
        self.transport = transport

    async def open(self, config: dict) -> OpenAiRealtimeSession:
        if (
            self.settings.api_key is None
            or not self.settings.api_key.get_secret_value()
        ):
            raise RuntimeError("OPENAI_API_KEY is required")
        if not isinstance(config, dict):
            raise ProviderPayloadError("Realtime session config must be an object")
        transport = self.transport or OpenAiRealtimeWebSocketTransport(
            self.settings, self.model
        )
        session = OpenAiRealtimeSession(
            transport, owns_transport=self.transport is None
        )
        await transport.send(
            {
                "type": "session.update",
                "session": {"type": "realtime", **config},
            }
        )
        return session


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProviderPayloadError("%s must be an object" % label)
    return value


def _error_message(payload: dict) -> str:
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return "OpenAI Realtime error: %s" % error["message"]
    return "OpenAI Realtime error"


def _usage(raw: dict) -> UsageReport:
    details = raw.get("input_token_details") or {}
    details = _object(details, "Realtime input token details")
    return UsageReport(
        prompt_tokens=_integer(raw, "input_tokens"),
        completion_tokens=_integer(raw, "output_tokens"),
        cached_prompt_tokens=_integer(details, "cached_tokens"),
    )


def _integer(raw: dict, name: str) -> int:
    value = raw.get(name, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProviderPayloadError("Realtime %s must be an integer" % name)
    return value
