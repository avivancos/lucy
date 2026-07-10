"""Raw OpenAI Chat Completions streaming adapter."""

from __future__ import annotations

import json
import time
from typing import AsyncIterator, Dict, List, Optional

import httpx

from lucy.llm import (
    LlmRequest,
    LlmStreamEvent,
    StreamEnd,
    TokenDelta,
    ToolCallDelta,
    ToolCallReady,
    UsageReport,
)
from lucy.testing.replay import RecordedFrame
from lucy.voice import ProviderPayloadError

from lucy_openai.settings import OpenAiSettings

CHAT_COMPLETIONS_PATH = "/chat/completions"
DONE_SENTINEL = "[DONE]"


class OpenAiSseTransport:
    def __init__(self, settings: OpenAiSettings) -> None:
        self.settings = settings
        self.frames: List[RecordedFrame] = []
        self._started = time.monotonic()
        self._client: Optional[httpx.AsyncClient] = None
        self._stream_context = None
        self._response = None
        self._lines = None

    def _at_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1_000)

    async def send(self, payload: dict) -> None:
        if self._response is not None:
            raise RuntimeError("OpenAI transport supports one request")
        if self.settings.api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required")
        self.frames.append(RecordedFrame("sent", self._at_ms(), dict(payload)))
        self._client = httpx.AsyncClient()
        self._stream_context = self._client.stream(
            "POST",
            self.settings.base_url.rstrip("/") + CHAT_COMPLETIONS_PATH,
            headers={
                "authorization": "Bearer %s" % self.settings.api_key.get_secret_value()
            },
            json=payload,
        )
        self._response = await self._stream_context.__aenter__()
        self._response.raise_for_status()
        self._lines = self._response.aiter_lines()

    async def receive(self) -> dict:
        if self._lines is None:
            raise RuntimeError("OpenAI request has not been sent")
        async for raw in self._lines:
            line = raw.strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                raise ProviderPayloadError("invalid OpenAI SSE frame")
            data = line[len("data:") :].strip()
            if data == DONE_SENTINEL:
                payload = {"data": DONE_SENTINEL}
            else:
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderPayloadError("invalid OpenAI SSE JSON") from exc
                payload = {"data": parsed}
            self.frames.append(RecordedFrame("received", self._at_ms(), payload))
            return payload
        raise ProviderPayloadError("OpenAI SSE stream ended before [DONE]")

    async def close(self) -> None:
        if self._stream_context is not None:
            await self._stream_context.__aexit__(None, None, None)
            self._stream_context = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class OpenAiLlmAdapter:
    def __init__(
        self,
        model: str,
        settings: Optional[OpenAiSettings] = None,
        transport=None,
    ) -> None:
        self.model = model
        self.settings = settings or OpenAiSettings()
        self.transport = transport

    def build_payload(self, request: LlmRequest) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                message.model_dump(exclude_none=True) for message in request.messages
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools is not None:
            payload["tools"] = request.tools
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_completion_tokens"] = request.max_output_tokens
        return payload

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        if (
            self.settings.api_key is None
            or not self.settings.api_key.get_secret_value()
        ):
            raise RuntimeError("OPENAI_API_KEY is required")
        transport = self.transport or OpenAiSseTransport(self.settings)
        owns_transport = self.transport is None
        tool_calls: Dict[int, Dict[str, str]] = {}
        usage: Optional[UsageReport] = None
        finish_reason: Optional[str] = None
        try:
            await transport.send(self.build_payload(request))
            while True:
                frame = await transport.receive()
                data = frame.get("data") if isinstance(frame, dict) else None
                if data == DONE_SENTINEL:
                    break
                chunk = _object(data, "OpenAI chunk")
                raw_usage = chunk.get("usage")
                if raw_usage is not None:
                    usage = _usage(_object(raw_usage, "OpenAI usage"))
                choices = chunk.get("choices", [])
                if not isinstance(choices, list):
                    raise ProviderPayloadError("OpenAI choices must be a list")
                for choice_value in choices:
                    choice = _object(choice_value, "OpenAI choice")
                    delta = _object(choice.get("delta", {}), "OpenAI delta")
                    content = delta.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise ProviderPayloadError(
                                "OpenAI content delta must be a string"
                            )
                        if content:
                            yield TokenDelta(text=content)
                    raw_tool_calls = delta.get("tool_calls", [])
                    if not isinstance(raw_tool_calls, list):
                        raise ProviderPayloadError("OpenAI tool_calls must be a list")
                    for raw_call in raw_tool_calls:
                        event = _tool_delta(
                            _object(raw_call, "OpenAI tool call"), tool_calls
                        )
                        if event is not None:
                            yield event
                    raw_finish = choice.get("finish_reason")
                    if raw_finish is not None:
                        if raw_finish not in {"stop", "tool_calls"}:
                            raise ProviderPayloadError(
                                "unsupported OpenAI finish reason"
                            )
                        finish_reason = raw_finish
        finally:
            if owns_transport and hasattr(transport, "close"):
                await transport.close()

        if finish_reason == "tool_calls":
            for index in sorted(tool_calls):
                state = tool_calls[index]
                if not state["call_id"] or not state["name"]:
                    raise ProviderPayloadError("incomplete OpenAI tool call")
                try:
                    arguments = json.loads(state["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    raise ProviderPayloadError("invalid OpenAI tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise ProviderPayloadError(
                        "OpenAI tool arguments must be an object"
                    )
                yield ToolCallReady(
                    call_id=state["call_id"],
                    name=state["name"],
                    arguments=arguments,
                )
        if usage is None:
            raise ProviderPayloadError("OpenAI stream omitted usage")
        yield usage
        yield StreamEnd(finish_reason=finish_reason or "stop")


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProviderPayloadError("%s must be an object" % label)
    return value


def _integer(raw: dict, name: str) -> int:
    value = raw.get(name, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProviderPayloadError("OpenAI %s must be an integer" % name)
    return value


def _usage(raw: dict) -> UsageReport:
    details = raw.get("prompt_tokens_details") or {}
    details = _object(details, "OpenAI prompt token details")
    return UsageReport(
        prompt_tokens=_integer(raw, "prompt_tokens"),
        completion_tokens=_integer(raw, "completion_tokens"),
        cached_prompt_tokens=_integer(details, "cached_tokens"),
    )


def _tool_delta(
    call: dict, states: Dict[int, Dict[str, str]]
) -> Optional[ToolCallDelta]:
    index = call.get("index", 0)
    if not isinstance(index, int) or isinstance(index, bool):
        raise ProviderPayloadError("OpenAI tool call index must be an integer")
    state = states.setdefault(index, {"call_id": "", "name": "", "arguments": ""})
    call_id = call.get("id")
    if call_id is not None:
        if not isinstance(call_id, str):
            raise ProviderPayloadError("OpenAI tool call id must be a string")
        state["call_id"] = call_id
    function = _object(call.get("function", {}), "OpenAI tool function")
    name = function.get("name")
    if name is not None:
        if not isinstance(name, str):
            raise ProviderPayloadError("OpenAI tool name must be a string")
        state["name"] = name
    arguments = function.get("arguments", "")
    if not isinstance(arguments, str):
        raise ProviderPayloadError("OpenAI tool arguments delta must be a string")
    if not arguments:
        return None
    state["arguments"] += arguments
    return ToolCallDelta(
        call_id=state["call_id"],
        name=name,
        arguments_delta=arguments,
    )
