"""Telephony control nodes; media and audio remain gateway-owned."""

from __future__ import annotations

from enum import Enum
from typing import Dict, Literal, Optional

from pydantic import Field

from lucy.nodes.base import NodeConfig, StateUpdate
from lucy.runtime import TurnContext
from lucy.state import ConversationState
from lucy.transport.schema import (
    AmdResult,
    Dial,
    Hold,
    SessionEnd,
    Transfer,
    TtsSpeak,
)

DTMF_TIMEOUT = "dtmf_timeout"


class TransferConfig(NodeConfig):
    deadline_ms: int = 100
    target: str
    mode: Literal["blind", "attended"]
    announce_template: Optional[str] = None


class DtmfMenuConfig(NodeConfig):
    deadline_ms: int = 5_000
    prompt_template: str
    options: Dict[str, str]
    timeout_ms: int = Field(gt=0)
    max_retries: int = Field(default=1, ge=0)


class VoicemailDetectConfig(NodeConfig):
    deadline_ms: int = 100


class DialConfig(NodeConfig):
    deadline_ms: int = 5_000
    target: str
    caller_id: str
    timeout_ms: int = Field(gt=0)


class HoldConfig(NodeConfig):
    deadline_ms: int = 5_000
    hold_ms: int = Field(ge=0)
    music: bool = False


class EndReason(str, Enum):
    COMPLETED = "completed"
    BOOKED = "booked"
    ESCALATED = "escalated"
    VOICEMAIL = "voicemail"
    FAILED = "failed"
    POLICY = "policy"


class EndCallConfig(NodeConfig):
    deadline_ms: int = 100
    reason: EndReason


class TransferNode:
    name = "transfer"

    def __init__(self, config: TransferConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if (
            self.config.mode == "attended"
            and self.config.announce_template is not None
            and ctx.emit is not None
        ):
            ctx.emit(
                TtsSpeak(
                    utterance_id="transfer-%s" % (ctx.turn_id or "turn"),
                    text=self.config.announce_template.format(**state.slots),
                )
            )
        if ctx.emit is not None:
            ctx.emit(Transfer(target=self.config.target))
        return {
            "agent_state": {
                **state.agent_state,
                "transfer_target": self.config.target,
            }
        }


class DtmfMenuNode:
    name = "dtmf_menu"

    def __init__(self, config: DtmfMenuConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        digits = [str(item) for item in ctx.payload.get("dtmf_digits", [])]
        selection = DTMF_TIMEOUT
        for attempt in range(self.config.max_retries + 1):
            if ctx.emit is not None:
                ctx.emit(
                    TtsSpeak(
                        utterance_id="dtmf-%s-%d"
                        % (ctx.turn_id or "turn", attempt + 1),
                        text=self.config.prompt_template.format(**state.slots),
                    )
                )
            if attempt < len(digits):
                candidate = self.config.options.get(digits[attempt])
                if candidate is not None:
                    selection = candidate
                    break
            await ctx.clock.sleep(self.config.timeout_ms / 1_000.0)
        return {
            "agent_state": {
                **state.agent_state,
                "dtmf_selection": selection,
            }
        }


class VoicemailDetectNode:
    name = "voicemail_detect"

    def __init__(self, config: Optional[VoicemailDetectConfig] = None) -> None:
        self.config = config or VoicemailDetectConfig()

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        raw = ctx.payload.get("amd_result")
        result = raw if isinstance(raw, AmdResult) else AmdResult.model_validate(raw)
        return {
            "agent_state": {
                **state.agent_state,
                "amd_outcome": result.outcome,
                "amd_confidence": result.confidence,
            }
        }


class DialNode:
    name = "dial"

    def __init__(self, config: DialConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if ctx.emit is not None:
            ctx.emit(
                Dial(
                    target=self.config.target,
                    caller_id=self.config.caller_id,
                    timeout_ms=self.config.timeout_ms,
                )
            )
        raw_outcome = ctx.payload.get("dial_outcome")
        if raw_outcome is None:
            await ctx.clock.sleep(self.config.timeout_ms / 1_000.0)
            outcome = "no_answer"
        else:
            outcome = str(raw_outcome)
        if outcome not in {"answered", "no_answer"}:
            raise ValueError("unknown dial outcome: %s" % outcome)
        return {"agent_state": {**state.agent_state, "dial_outcome": outcome}}


class HoldNode:
    name = "hold"

    def __init__(self, config: HoldConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if ctx.emit is not None:
            ctx.emit(Hold(state="hold", music=self.config.music))
        await ctx.clock.sleep(self.config.hold_ms / 1000.0)
        if ctx.emit is not None:
            ctx.emit(Hold(state="resume", music=self.config.music))
        return {"agent_state": dict(state.agent_state)}


class EndCallNode:
    name = "end_call"

    def __init__(self, config: EndCallConfig) -> None:
        self.config = config

    async def __call__(self, state: ConversationState, ctx: TurnContext) -> StateUpdate:
        if ctx.emit is not None:
            ctx.emit(SessionEnd(reason=self.config.reason.value))
        return {
            "agent_state": {
                **state.agent_state,
                "end_reason": self.config.reason.value,
            }
        }
