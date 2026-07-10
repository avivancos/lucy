"""Raw Deepgram live transcription WebSocket adapter."""

from __future__ import annotations

import base64
import json
import time
from typing import List, Optional
from urllib.parse import urlencode

import websockets

from lucy.testing.replay import RecordedFrame
from lucy.voice import AudioChunk, ProviderPayloadError, TranscriptEvent

from lucy_deepgram.settings import DeepgramSettings

AUDIO_FIELD = "audio"
BINARY_FIELD = "b64"


class DeepgramWebSocketTransport:
    def __init__(self, settings: DeepgramSettings, model: str) -> None:
        self.settings = settings
        self.model = model
        self.frames: List[RecordedFrame] = []
        self._started = time.monotonic()
        self._socket = None

    async def _connect(self):
        if self._socket is not None:
            return self._socket
        if self.settings.api_key is None:
            raise RuntimeError("DEEPGRAM_API_KEY is required")
        query = urlencode(
            {
                "model": self.model,
                "encoding": self.settings.encoding,
                "sample_rate": self.settings.sample_rate,
                "channels": self.settings.channels,
                "interim_results": str(self.settings.interim_results).lower(),
                "endpointing": self.settings.endpointing_ms,
            }
        )
        self._socket = await websockets.connect(
            "%s?%s" % (self.settings.realtime_url, query),
            additional_headers={
                "Authorization": "Token %s" % self.settings.api_key.get_secret_value()
            },
        )
        return self._socket

    def _at_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1_000)

    async def send(self, payload: dict) -> None:
        socket = await self._connect()
        audio = payload.get(AUDIO_FIELD)
        if isinstance(audio, dict) and isinstance(audio.get(BINARY_FIELD), str):
            try:
                wire = base64.b64decode(audio[BINARY_FIELD], validate=True)
            except ValueError as exc:
                raise ProviderPayloadError("invalid Deepgram audio frame") from exc
            await socket.send(wire)
        else:
            await socket.send(json.dumps(payload))
        self.frames.append(RecordedFrame("sent", self._at_ms(), dict(payload)))

    async def receive(self) -> dict:
        socket = await self._connect()
        try:
            payload = json.loads(await socket.recv())
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderPayloadError("invalid Deepgram JSON frame") from exc
        if not isinstance(payload, dict):
            raise ProviderPayloadError("invalid Deepgram frame shape")
        self.frames.append(RecordedFrame("received", self._at_ms(), dict(payload)))
        return payload

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None


class DeepgramSttAdapter:
    def __init__(
        self,
        model: str,
        settings: Optional[DeepgramSettings] = None,
        transport=None,
    ) -> None:
        self.model = model
        self.settings = settings or DeepgramSettings()
        self.transport = transport

    async def transcribe(self, chunks: List[AudioChunk]) -> List[TranscriptEvent]:
        if not chunks:
            raise ProviderPayloadError("Deepgram transcription requires audio")
        if (
            self.settings.api_key is None
            or not self.settings.api_key.get_secret_value()
        ):
            raise RuntimeError("DEEPGRAM_API_KEY is required")

        owns_transport = self.transport is None
        transport = self.transport or DeepgramWebSocketTransport(
            self.settings, self.model
        )
        events: List[TranscriptEvent] = []
        final_parts: List[str] = []
        try:
            for chunk in chunks:
                for offset in range(0, len(chunk.data), self.settings.frame_bytes):
                    frame = chunk.data[offset : offset + self.settings.frame_bytes]
                    await transport.send(
                        {
                            AUDIO_FIELD: {
                                BINARY_FIELD: base64.b64encode(frame).decode("ascii"),
                                "codec": self.settings.encoding,
                            }
                        }
                    )
            await transport.send({"type": "Finalize"})
            while True:
                payload = await transport.receive()
                if payload.get("type") != "Results":
                    continue
                transcript, is_final, speech_final, from_finalize = _result(payload)
                if not transcript:
                    continue
                if is_final:
                    final_parts.append(transcript)
                else:
                    events.append(
                        TranscriptEvent(
                            session_id=chunks[0].session_id,
                            text=transcript,
                            is_final=False,
                            sequence=min(len(events), chunks[-1].sequence),
                        )
                    )
                if final_parts and (is_final or speech_final or from_finalize):
                    break
            await transport.send({"type": "CloseStream"})
        finally:
            if owns_transport and hasattr(transport, "close"):
                await transport.close()

        final_text = " ".join(final_parts).strip()
        if not final_text:
            raise ProviderPayloadError("Deepgram returned no final transcript")
        events.append(
            TranscriptEvent(
                session_id=chunks[0].session_id,
                text=final_text,
                is_final=True,
                sequence=chunks[-1].sequence,
            )
        )
        return events


def _result(payload: dict) -> tuple[str, bool, bool, bool]:
    channel = payload.get("channel")
    if not isinstance(channel, dict):
        raise ProviderPayloadError("invalid Deepgram result channel")
    alternatives = channel.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        raise ProviderPayloadError("invalid Deepgram alternatives")
    first = alternatives[0]
    if not isinstance(first, dict) or not isinstance(first.get("transcript"), str):
        raise ProviderPayloadError("invalid Deepgram transcript")
    markers = [
        payload.get("is_final", False),
        payload.get("speech_final", False),
        payload.get("from_finalize", False),
    ]
    if not all(isinstance(marker, bool) for marker in markers):
        raise ProviderPayloadError("invalid Deepgram final markers")
    return (first["transcript"], markers[0], markers[1], markers[2])
