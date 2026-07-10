"""Canonical control-channel golden fixture generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Union

from pydantic import BaseModel

from lucy.transport.schema import (
    AmdResult,
    BargeIn,
    Dial,
    Dtmf,
    DtmfSend,
    Envelope,
    Hold,
    RecordingFailed,
    RecordingStart,
    RecordingStarted,
    RecordingStop,
    RecordingUploaded,
    RealtimeConnect,
    RealtimeToolResult,
    SessionConfigure,
    SessionEnd,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    Transfer,
    TransportMetrics,
    TtsCancel,
    TtsPlayback,
    TtsSpeak,
    TtsStreamEnd,
    VadSpeechEnd,
    VadSpeechStart,
    to_wire,
)

GOLDEN_SESSION_ID = "sess-golden"
GOLDEN_TURN_ID = "turn-1"
GOLDEN_TS_MS = 1_700_000_000_000


def canonical_dumps(value: Union[dict, BaseModel]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def golden_messages() -> Dict[str, dict]:
    samples = {
        "session.started": SessionStarted(
            transport="fixture",
            caller="fixture-caller",
            codecs=["pcm16/8000"],
            features=["recording"],
        ),
        "vad.speech_start": VadSpeechStart(at_ms=100),
        "vad.speech_end": VadSpeechEnd(at_ms=500, speech_ms=400),
        "stt.partial": SttPartial(text="hello", stability=0.5, provider="fixture"),
        "stt.final": SttFinal(text="hello world", provider="fixture", stt_ms=20),
        "dtmf": Dtmf(digit="1"),
        "amd.result": AmdResult(outcome="human", confidence=0.9),
        "tts.playback": TtsPlayback(utterance_id="utt-1", state="mark", mark_chars=5),
        "barge_in": BargeIn(at_ms=600, during="speaking", utterance_id="utt-1"),
        "transport.metrics": TransportMetrics(
            jitter_ms=1.5, rtt_ms=12.0, packet_loss=0.0
        ),
        "session.ended": SessionEnded(reason="fixture_complete"),
        "recording.started": RecordingStarted(
            recording_id="rec-1",
            leg="mixed",
            blob_id="blob-1",
            consent_ref="consent-1",
        ),
        "recording.uploaded": RecordingUploaded(
            recording_id="rec-1",
            leg="mixed",
            blob_id="blob-1",
            upload_url_ref="upload-ref-1",
            duration_ms=1000,
            byte_count=32000,
            sha256="a" * 64,
            container="wav",
            consent_ref="consent-1",
        ),
        "recording.failed": RecordingFailed(
            recording_id="rec-1", error_code="upload_failed", retryable=True
        ),
        "session.configure": SessionConfigure(
            stt="fixture", tts="fixture", vad="server"
        ),
        "tts.speak": TtsSpeak(utterance_id="utt-1", text="Hello.", flush=True),
        "tts.cancel": TtsCancel(utterance_id="utt-1"),
        "tts.stream_end": TtsStreamEnd(),
        "realtime.connect": RealtimeConnect(provider="fixture", model="live-1"),
        "realtime.tool_result": RealtimeToolResult(
            call_id="call-1", output_json='{"ok":true}'
        ),
        "dtmf.send": DtmfSend(digits="12#"),
        "transfer": Transfer(target="support"),
        "dial": Dial(target="destination", caller_id="caller", timeout_ms=5000),
        "hold": Hold(state="hold", music=True),
        "session.end": SessionEnd(reason="completed"),
        "recording.start": RecordingStart(
            recording_id="rec-1",
            leg="mixed",
            blob_id="blob-1",
            upload_url_ref="upload-ref-1",
            container="wav",
            consent_ref="consent-1",
        ),
        "recording.stop": RecordingStop(recording_id="rec-1"),
    }
    messages = {}
    for seq, (type_, payload) in enumerate(sorted(samples.items()), start=1):
        envelope = Envelope(
            type=type_,
            session_id=GOLDEN_SESSION_ID,
            turn_id=GOLDEN_TURN_ID,
            seq=seq,
            ts_ms=GOLDEN_TS_MS,
        )
        messages[type_] = to_wire(envelope, payload)
    return messages


def write_golden(directory: Path) -> List[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for type_, message in sorted(golden_messages().items()):
        path = directory / (type_ + ".json")
        path.write_text(canonical_dumps(message) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m lucy.transport.golden <directory>")
    write_golden(Path(sys.argv[1]))
