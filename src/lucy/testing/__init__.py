"""Deterministic in-process simulators and fixtures (ADR 0003 no-mocks policy).

This is the public, documented home for Lucy's local provider simulators and
test fixtures, so SDK users and plugin authors share one source of truth. The
classes formerly lived in ``lucy.voice``, ``lucy.mcp``, ``lucy.rag``,
``lucy.observe`` and ``lucy.metrics``; importing them from those modules still
works for one release but emits ``DeprecationWarning``.
"""

from __future__ import annotations

from lucy.testing.checkpoint import check_checkpoint_store
from lucy.testing.mcp import LocalMcpCommandTransport
from lucy.testing.metrics import LocalMetricEventChannel
from lucy.testing.observe import InMemoryOtelSpanExporter, InMemoryTraceExporter
from lucy.testing.rag import LocalEmbeddingFixture
from lucy.testing.recording import RecordingBlobStoreSimulator
from lucy.testing.realtime import (
    LocalRealtimeSimulator,
    ScriptedRealtimeToolCall,
    ScriptedRealtimeTurn,
)
from lucy.testing.voice import LocalSttSimulator, LocalTtsSimulator

__all__ = [
    "LocalSttSimulator",
    "LocalTtsSimulator",
    "LocalMcpCommandTransport",
    "LocalEmbeddingFixture",
    "InMemoryOtelSpanExporter",
    "InMemoryTraceExporter",
    "LocalMetricEventChannel",
    "LocalRealtimeSimulator",
    "RecordingBlobStoreSimulator",
    "ScriptedRealtimeToolCall",
    "ScriptedRealtimeTurn",
    "check_checkpoint_store",
]
