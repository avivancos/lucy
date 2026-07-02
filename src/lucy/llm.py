"""Streaming LLM contract and providers (ADR 0011, ADR 0005).

`LlmProvider.stream_chat` yields a typed stream-event union - the frozen
contract that the cascaded turn driver (this card), tool rounds (card 34),
speculation (card 36), and the speech-to-speech driver (card 38) build on.
One OpenAI-compatible adapter covers hosted and self-hosted (vLLM/SGLang)
engines; a deterministic in-process simulator is the no-mocks test double.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Literal, Optional, Protocol, Union

import httpx
from pydantic import BaseModel, ConfigDict, Field

from lucy.clock import Clock
from lucy.providers import Capability, ModelInfo, ModelRegistry


# -- request models ----------------------------------------------------------


class LlmMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: Optional[str] = None


class LlmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    messages: List[LlmMessage] = Field(min_length=1)
    tools: Optional[List[dict]] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    cache_key: Optional[str] = None  # prompt-cache hint; carried now, used in card 36


# -- stream event union ------------------------------------------------------


@dataclass(frozen=True)
class TokenDelta:
    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    call_id: str
    name: Optional[str]
    arguments_delta: str


@dataclass(frozen=True)
class ToolCallReady:
    call_id: str
    name: str
    arguments: Dict[str, object]


@dataclass(frozen=True)
class UsageReport:
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int = 0


@dataclass(frozen=True)
class StreamEnd:
    finish_reason: Literal["stop", "tool_calls", "cancelled", "error"]


LlmStreamEvent = Union[TokenDelta, ToolCallDelta, ToolCallReady, UsageReport, StreamEnd]


class LlmProvider(Protocol):
    def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        ...


# -- model resolution --------------------------------------------------------


class LlmModelNotRegistered(ValueError):
    """Raised when a (provider, model) pair is absent or lacks Capability.LLM."""


def resolve_llm(registry: ModelRegistry, provider: str, model: str) -> ModelInfo:
    info = registry.get(provider, model)
    if info is None or Capability.LLM not in info.capabilities:
        raise LlmModelNotRegistered(
            "no LLM-capable model %r/%r in the registry" % (provider, model)
        )
    return info


# -- deterministic in-process simulator (ADR 0003) ---------------------------


@dataclass
class ScriptedLlmTurn:
    tokens: List[str]
    usage: UsageReport
    finish_reason: str = "stop"
    # Tool calls this turn emits after its tokens (card 34). Each yields one
    # ToolCallDelta (arguments as a single JSON fragment) then ToolCallReady;
    # script finish_reason="tool_calls" on such a turn.
    tool_calls: List[ToolCallReady] = field(default_factory=list)


class LocalLlmSimulator:
    """Real in-process ``LlmProvider``: each ``stream_chat`` consumes the next
    scripted turn, emitting one ``TokenDelta`` per token paced by the injected
    clock, then the ``UsageReport``, then ``StreamEnd``. Consumer cancellation
    propagates ``CancelledError`` and sets ``cancelled``."""

    def __init__(
        self,
        turns: List[ScriptedLlmTurn],
        clock: Clock,
        token_interval_ms: float,
    ) -> None:
        self._turns = list(turns)
        self._clock = clock
        self._interval_s = token_interval_ms / 1000.0
        self._index = 0
        self.cancelled = False

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        turn = self._turns[self._index]
        self._index += 1
        try:
            for token in turn.tokens:
                await self._clock.sleep(self._interval_s)
                yield TokenDelta(text=token)
            for call in turn.tool_calls:
                yield ToolCallDelta(
                    call_id=call.call_id,
                    name=call.name,
                    arguments_delta=json.dumps(call.arguments),
                )
                yield ToolCallReady(
                    call_id=call.call_id, name=call.name, arguments=call.arguments
                )
            yield turn.usage
            yield StreamEnd(finish_reason=turn.finish_reason)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            self.cancelled = True
            raise


# -- OpenAI-compatible adapter (ADR 0005: also covers vLLM/SGLang) ------------


class OpenAiCompatibleAdapter:
    """`LlmProvider` over the OpenAI chat-completions SSE wire. ``base_url`` is
    always injected - no endpoint literal lives in ``src``. Emits usage before
    the terminal ``StreamEnd`` to match the simulator's event order."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        http_client: Optional["httpx.AsyncClient"] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = http_client

    def _payload(self, request: LlmRequest) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "model": request.model,
            "messages": [m.model_dump(exclude_none=True) for m in request.messages],
            "stream": True,
        }
        if request.tools is not None:
            payload["tools"] = request.tools
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        return payload

    async def stream_chat(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        headers = {}
        if self._api_key:
            headers["authorization"] = "Bearer %s" % self._api_key

        tool_args: Dict[int, Dict[str, str]] = {}
        finish_reason: Optional[str] = None
        usage: Optional[UsageReport] = None
        malformed = False
        try:
            async with client.stream(
                "POST",
                self._base_url + "/chat/completions",
                json=self._payload(request),
                headers=headers,
            ) as response:
                async for raw in response.aiter_lines():
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        # The whole chunk is untrusted upstream output: a bad
                        # frame OR a bad field shape (non-object chunk, non-int
                        # usage counts or tool index, non-str content) fails
                        # safe - terminate with a typed error StreamEnd, never
                        # crash the turn.
                        chunk = json.loads(data)
                        raw_usage = chunk.get("usage")
                        if raw_usage:
                            usage = _usage_from(raw_usage)
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                if not isinstance(content, str):
                                    raise ValueError("non-string content delta")
                                yield TokenDelta(text=content)
                            for call in delta.get("tool_calls") or []:
                                async for event in self._tool_delta(
                                    call, tool_args
                                ):
                                    yield event
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                    except (
                        json.JSONDecodeError,
                        TypeError,
                        ValueError,
                        AttributeError,
                    ):
                        malformed = True
                        break
        finally:
            if owns_client:
                await client.aclose()

        if malformed:
            yield StreamEnd(finish_reason="error")
            return
        if finish_reason == "tool_calls":
            for state in tool_args.values():
                try:
                    arguments = json.loads(state["args"] or "{}")
                except json.JSONDecodeError:
                    # Assembled tool arguments are upstream data too: same
                    # fail-safe termination, no partial ToolCallReady.
                    yield StreamEnd(finish_reason="error")
                    return
                if not isinstance(arguments, dict):
                    # Valid JSON but not an object: still upstream garbage.
                    yield StreamEnd(finish_reason="error")
                    return
                yield ToolCallReady(
                    call_id=state["call_id"],
                    name=state["name"],
                    arguments=arguments,
                )
        if usage is not None:
            yield usage
        yield StreamEnd(finish_reason=finish_reason or "stop")  # type: ignore[arg-type]

    async def _tool_delta(
        self, call: Dict[str, object], tool_args: Dict[int, Dict[str, str]]
    ) -> AsyncIterator[LlmStreamEvent]:
        index = int(call.get("index", 0))
        state = tool_args.setdefault(index, {"call_id": "", "name": "", "args": ""})
        if call.get("id"):
            state["call_id"] = str(call["id"])
        function = call.get("function") or {}
        if function.get("name"):
            state["name"] = str(function["name"])
        arguments_delta = function.get("arguments", "")
        if arguments_delta:
            state["args"] += arguments_delta
            yield ToolCallDelta(
                call_id=state["call_id"],
                name=function.get("name"),
                arguments_delta=arguments_delta,
            )


def _usage_from(raw: Dict[str, object]) -> UsageReport:
    details = raw.get("prompt_tokens_details") or {}
    return UsageReport(
        prompt_tokens=int(raw.get("prompt_tokens", 0)),
        completion_tokens=int(raw.get("completion_tokens", 0)),
        cached_prompt_tokens=int(details.get("cached_tokens", 0)),
    )
