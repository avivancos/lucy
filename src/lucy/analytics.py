"""Portable in-process analytics derived from telemetry wire v1."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from lucy.observe.events import TurnEvent


class TalkMeasures(BaseModel):
    """Additive measured speech durations for a telemetry rollup."""

    caller_talk_ms: int = Field(default=0, ge=0)
    agent_talk_ms: int = Field(default=0, ge=0)

    @computed_field
    def talk_ratio(self) -> Optional[float]:
        total = self.agent_talk_ms + self.caller_talk_ms
        if total == 0:
            return None
        return self.agent_talk_ms / total


def aggregate_talk_measures(turns: Iterable[TurnEvent]) -> TalkMeasures:
    """Sum only measured turn counters; never infer speech from other fields."""

    caller_talk_ms = 0
    agent_talk_ms = 0
    for turn in turns:
        caller_talk_ms += turn.caller_talk_ms
        agent_talk_ms += turn.agent_talk_ms
    return TalkMeasures(
        caller_talk_ms=caller_talk_ms,
        agent_talk_ms=agent_talk_ms,
    )
