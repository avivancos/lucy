# CPaaS media-stream replay fixtures

Each non-empty JSONL line is one complete provider WebSocket JSON frame. Tests
read and replay each line byte-for-byte; the lines are compact JSON with no
wrapper, timestamp, or test-only envelope.

## Provenance and redaction

The frame shapes were transcribed from the official provider media-stream
references and reduced to a minimal handshake (`connected`, `start`, `media`,
`stop`) replay transcript:

- Telnyx: <https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming>
- Twilio: <https://www.twilio.com/docs/voice/media-streams/websocket-messages>

All call, account, stream, session, and user identifiers are stable
`<redacted-...>` tokens. Phone numbers are removed. The media payload is the
base64 encoding of one zero byte (`AA==`), not caller audio. No API key,
account identifier, phone number, provider dashboard URL, or call audio is
present. The fixtures preserve documented wire shape and ordering for local
replay; a live capture must be re-recorded through the credential-safe recorder
before production adapter integration.

## Schema notes

Telnyx uses snake_case fields such as `sequence_number`, `stream_id`, and
`media_format`; Twilio uses camelCase `sequenceNumber`, `streamSid`, and
`mediaFormat`. Both fixtures contain inbound PCMU/mu-law 8 kHz media and a
terminal `stop` event. The Rust media adapter owns decoding these frames; Python
must receive control events only.
