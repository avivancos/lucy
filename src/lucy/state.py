"""Conversation state and checkpoint-store contracts for AgentGraph turns."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from lucy.specs import FunnelStage


class StateKeyError(KeyError):
    """Raised when a node returns an update for an unknown state key."""


class TranscriptLine(BaseModel):
    speaker: Literal["caller", "agent"]
    text: str


class ConversationState(BaseModel):
    transcript: List[TranscriptLine] = Field(default_factory=list)
    funnel_stage: Optional[FunnelStage] = None
    slots: Dict[str, str] = Field(default_factory=dict)
    tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    turns: int = 0
    agent_state: Dict[str, Any] = Field(default_factory=dict)

    def merged(self, update: Dict[str, Any]) -> "ConversationState":
        known = set(type(self).model_fields)
        unknown = set(update) - known
        if unknown:
            raise StateKeyError(next(iter(sorted(unknown))))
        data = self.model_dump(mode="python")
        data.update(update)
        return type(self).model_validate(data)


class Checkpoint(BaseModel):
    checkpoint_id: str
    session_id: str
    thread_id: str
    turn_id: str
    superstep: int
    kind: Literal["superstep", "turn_final"]
    state: ConversationState
    created_at_ms: int

    @model_validator(mode="before")
    @classmethod
    def default_thread_to_session(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("thread_id"):
            data = {**data, "thread_id": data.get("session_id", "")}
        return data


class CheckpointStore(Protocol):
    async def save(self, checkpoint: Checkpoint) -> None: ...

    async def load_latest(self, thread_id: str) -> Optional[Checkpoint]: ...

    async def history(self, thread_id: str) -> List[Checkpoint]: ...


def checkpoint_id(session_id: str, turn_id: str, superstep: int) -> str:
    return "%s:%s:%d" % (session_id, turn_id, superstep)


def _copy_checkpoint(checkpoint: Checkpoint) -> Checkpoint:
    return Checkpoint.model_validate_json(checkpoint.model_dump_json())


class InMemoryCheckpointStore:
    """Deterministic in-process checkpoint store for tests and local runs."""

    def __init__(self) -> None:
        self._items: List[Checkpoint] = []

    async def save(self, checkpoint: Checkpoint) -> None:
        copied = _copy_checkpoint(checkpoint)
        for index, item in enumerate(self._items):
            if item.checkpoint_id == copied.checkpoint_id:
                self._items[index] = copied
                return
        self._items.append(copied)

    async def load_latest(self, thread_id: str) -> Optional[Checkpoint]:
        for item in reversed(self._items):
            if item.thread_id == thread_id:
                return _copy_checkpoint(item)
        return None

    async def history(self, thread_id: str) -> List[Checkpoint]:
        return [
            _copy_checkpoint(item)
            for item in self._items
            if item.thread_id == thread_id
        ]
