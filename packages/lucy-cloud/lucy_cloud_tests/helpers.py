from __future__ import annotations

import uuid


def wire_event(index: int = 1, *, payload: str = "") -> dict:
    return {
        "type": "turn",
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"wire-event-{index}")),
        "session_id": "session-1",
        "emitted_at_ms": index,
        "tags": {},
        "turn_id": f"turn-{index}",
        "turn_index": index,
        "latency_waterfall": {
            "stt_ms": 0.0,
            "rag_ms": 0.0,
            "llm_ms": 0.0,
            "mcp_tools_ms": 0.0,
            "tts_ms": 0.0,
            "transport_ms": 0.0,
        },
        "interrupted": False,
        "timeout_events": [],
        "payload": payload,
    }
