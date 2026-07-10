"""Raw ElevenLabs stream-input WebSocket TTS adapter."""

from __future__ import annotations

import base64
import json
import time
from typing import List, Optional
from urllib.parse import urlencode

import websockets

from lucy.testing.replay import RecordedFrame
from lucy.voice import ProviderPayloadError, TtsStreamEvent

from lucy_elevenlabs.catalog import MODEL_IDS
from lucy_elevenlabs.settings import ElevenLabsSettings

STREAM_INPUT_PATH = "/v1/text-to-speech/{voice_id}/stream-input"


class ElevenLabsWebSocketTransport:
    def __init__(self, settings: ElevenLabsSettings, model: str) -> None:
        self.settings = settings
        self.model = model
        self.frames: List[RecordedFrame] = []
        self.audio = bytearray()
        self._started = time.monotonic()
        self._socket = None

    async def _connect(self):
        if self._socket is not None:
            return self._socket
        if not self.settings.voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID is required")
        model_id = MODEL_IDS.get(self.model)
        if model_id is None:
            raise ValueError("unsupported ElevenLabs model %r" % self.model)
        query = urlencode(
            {
                "model_id": model_id,
                "output_format": self.settings.output_format,
                "sync_alignment": str(self.settings.sync_alignment).lower(),
            }
        )
        url = "%s%s?%s" % (
            self.settings.ws_url.rstrip("/"),
            STREAM_INPUT_PATH.format(voice_id=self.settings.voice_id),
            query,
        )
        self._socket = await websockets.connect(url)
        return self._socket

    def _at_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1_000)

    async def send(self, payload: dict) -> None:
        socket = await self._connect()
        await socket.send(json.dumps(payload))
        self.frames.append(RecordedFrame("sent", self._at_ms(), dict(payload)))

    async def receive(self) -> dict:
        socket = await self._connect()
        try:
            raw = await socket.recv()
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderPayloadError("invalid ElevenLabs JSON frame") from exc
        if not isinstance(payload, dict):
            raise ProviderPayloadError("invalid ElevenLabs frame shape")
        recorded = dict(payload)
        audio = payload.get("audio")
        if isinstance(audio, str) and audio:
            try:
                decoded = base64.b64decode(audio, validate=True)
            except ValueError as exc:
                raise ProviderPayloadError("invalid ElevenLabs audio payload") from exc
            self.audio.extend(decoded)
            recorded["audio"] = {
                "b64": audio,
                "codec": self.settings.output_format,
            }
        self.frames.append(RecordedFrame("received", self._at_ms(), recorded))
        return payload

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None


class ElevenLabsTtsAdapter:
    def __init__(
        self,
        model: str,
        settings: Optional[ElevenLabsSettings] = None,
        transport=None,
    ) -> None:
        self.model = model
        self.settings = settings or ElevenLabsSettings()
        self.transport = transport

    async def synthesize(self, session_id: str, text: str) -> List[TtsStreamEvent]:
        if not text.strip():
            raise ProviderPayloadError("tts text cannot be blank")
        if (
            self.settings.api_key is None
            or not self.settings.api_key.get_secret_value()
        ):
            raise RuntimeError("ELEVENLABS_API_KEY is required")
        if not self.settings.voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID is required")

        owns_transport = self.transport is None
        transport = self.transport or ElevenLabsWebSocketTransport(
            self.settings, self.model
        )
        events = [
            TtsStreamEvent(session_id=session_id, status="started", chunk_text="")
        ]
        try:
            await transport.send(
                {
                    "text": " ",
                    "xi_api_key": self.settings.api_key.get_secret_value(),
                }
            )
            await transport.send({"text": text, "flush": True})
            await transport.send({"text": ""})
            while True:
                payload = await transport.receive()
                if not isinstance(payload, dict):
                    raise ProviderPayloadError("invalid ElevenLabs frame shape")
                chunk_text = _alignment_text(payload)
                if chunk_text:
                    events.append(
                        TtsStreamEvent(
                            session_id=session_id,
                            status="chunk",
                            chunk_text=chunk_text,
                        )
                    )
                is_final = payload.get("isFinal", False)
                if is_final is None:
                    is_final = False
                if not isinstance(is_final, bool):
                    raise ProviderPayloadError("invalid ElevenLabs final marker")
                if is_final:
                    break
        finally:
            if owns_transport and hasattr(transport, "close"):
                await transport.close()
        events.append(
            TtsStreamEvent(session_id=session_id, status="finished", chunk_text="")
        )
        return events


def _alignment_text(payload: dict) -> str:
    alignment = payload.get("normalizedAlignment") or payload.get("alignment")
    if alignment is None:
        return ""
    if not isinstance(alignment, dict):
        raise ProviderPayloadError("invalid ElevenLabs alignment")
    chars = alignment.get("chars", [])
    if not isinstance(chars, list) or not all(isinstance(item, str) for item in chars):
        raise ProviderPayloadError("invalid ElevenLabs alignment characters")
    return "".join(chars)
