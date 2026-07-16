# CPaaS PSTN adapter

Lucy supports `cpaas/telnyx` and `cpaas/twilio` through the same registry used
by `VoiceSpec`. The Rust gateway owns every provider media frame. Python sees
only wire-v1 control events and directives; phone numbers and audio never enter
the control channel.

## Provider capabilities

| Provider | Inbound media | Outbound media | Barge-in | DTMF | Hangup |
| --- | --- | --- | --- | --- | --- |
| Telnyx | PCMU/8 kHz or L16/16 kHz | base64 RTP payload | `clear` | inbound | Call Control hangup action |
| Twilio | mu-law/8 kHz | base64 mu-law/8 kHz | `clear` | inbound | Call resource `Status=completed` |

The gateway converts media to PCM16 little-endian before the STT/VAD backend.
Telnyx chunks pass through a bounded reorder window. Outbound audio is paced by
the shared media session and correlated with provider `mark` events. A
`session.end` directive calls the provider hangup API before closing the media
WebSocket; closing the stream alone is not treated as a completed hangup.

Protocol references:

- <https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming>
- <https://developers.telnyx.com/api-reference/call-commands/streaming-start>
- <https://developers.telnyx.com/api-reference/call-commands/hangup-call>
- <https://www.twilio.com/docs/voice/media-streams/websocket-messages>
- <https://www.twilio.com/docs/global-infrastructure/firewall-configurations/media-streams-configuration>
- <https://www.twilio.com/docs/voice/api/call-resource>

## Authentication and deployment

The public media URL must be `wss://`; plain `ws://` and HTTP provider APIs are
accepted only for explicit loopback protocol tests. URLs containing user info,
query parameters, or fragments are rejected.

- Telnyx must be configured with `stream_auth_token`. Lucy compares the
  `x-telnyx-streaming-auth-token` upgrade header in constant time.
- Twilio must send `X-Twilio-Signature`. Lucy verifies HMAC-SHA1 over the exact
  configured public WebSocket URL with the account auth token.
- The gateway applies `LUCY_CPAAAS_ALLOWED_CIDRS` to its immediate TCP peer.
  For direct provider connections this may be the provider range. Behind a TLS
  terminator or reverse proxy it must be the trusted proxy range; the edge
  firewall or proxy owns provider-source CIDR enforcement before forwarding.
  Lucy does not trust forwarded-address headers. Signature or token validation
  remains mandatory at the gateway in every topology.
- The outbound Lucy control WebSocket requires the independent
  `LUCY_GATEWAY_CONTROL_TOKEN` Bearer credential.

Configure the gateway with deployment secrets, not committed `.env` values:

```text
LUCY_CPAAAS_PROVIDER=telnyx
LUCY_CPAAAS_PUBLIC_WS_URL=wss://voice.example.com/cpaas/telnyx
LUCY_CPAAAS_STREAM_AUTH_TOKEN=<secret>
LUCY_CPAAAS_API_BASE_URL=https://api.example-provider.com
LUCY_CPAAAS_ACCOUNT_ID=<connection-or-account-id>
LUCY_CPAAAS_API_KEY=<secret>
LUCY_CPAAAS_COUNTRY_CODE=ES
LUCY_CPAAAS_MEDIA_BIND=0.0.0.0:9094
LUCY_CPAAAS_MEDIA_HOST_PORT=19094
LUCY_SESSION_WS_URL=wss://lucy.example.com/v1/session/ws
LUCY_GATEWAY_CONTROL_TOKEN=<independent-secret>
```

Provider/model URLs, codecs, credentials, timeouts, CIDRs, and session limits
come from typed settings or named registry constants. The example API hostname
above is deliberately not an executable provider default.

## Deterministic conformance

The committed JSONL fixtures contain compact, byte-replayable frames derived
from the official provider references. Identifiers and phone numbers are
redacted and the media byte is synthetic silence. They are protocol fixtures,
not represented as live call captures.

Run the adapter, authentication, codec, call-control, and replay gates with:

```bash
docker compose --profile gateway-it run --rm lucy-gateway-tests \
  cargo test --test cpaas_media_stream --test cpaas_call_control \
  --test cpaas_runtime
```

The replay opens real local WebSocket and HTTP servers. It sends the fixture
lines byte-for-byte, authenticates the provider upgrade, drives Lucy playback,
and verifies the provider hangup requests without a mocking framework.

## Optional live PSTN smoke

Set the following only in a gitignored `.env` or deployment secret store:

```text
LUCY_CPAAAS_PROVIDER=
LUCY_CPAAAS_ACCOUNT_ID=
LUCY_CPAAAS_API_KEY=
LUCY_CPAAAS_FROM_NUMBER=
LUCY_CPAAAS_TO_NUMBER=
LUCY_CPAAAS_API_BASE_URL=
LUCY_CPAAAS_PUBLIC_WS_URL=
LUCY_CPAAAS_STREAM_AUTH_TOKEN=
LUCY_CPAAAS_COUNTRY_CODE=
```

For Twilio, `LUCY_CPAAAS_ACCOUNT_ID` is the Account SID and the API key value
is the Auth Token. The media gateway uses the same Auth Token as
`LUCY_CPAAAS_STREAM_AUTH_TOKEN` for signature verification. For Telnyx, the
account field is the Call Control connection ID and the stream token is an
independent generated secret.

Run:

```bash
docker compose --profile cpaas-smoke run --rm cpaas-smoke
```

The public WSS terminator must forward to the loopback-only host port selected
by `LUCY_CPAAAS_MEDIA_HOST_PORT` (default `19094`). Start the ordinary
`lucy-api` and `lucy-media-gateway` services with the same provider, country,
public URL, stream token, API credential, and independent control token before
originating the smoke call.

When configuration is absent, the command exits successfully and names only
the missing environment variables. It never prints phone numbers, API keys,
stream tokens, provider response bodies, or call identifiers. An accepted API
request proves PSTN origination; a complete release check also confirms that
the public gateway received the authenticated media stream and emitted a clean
`session.ended` event.

Card 42 proves the adapter with deterministic local WebSocket/HTTP servers and
official-reference fixtures. It does not claim a verified public media path:
Card 101 owns the real TLS/proxy diagnosis plus authenticated Telnyx and Twilio
media, playback, and clean-stop captures required for that release evidence.

## Spain-specific constraints

- A Spanish geographic `+34` DID normally requires identity or company
  documents plus recent proof of address in the matching province.
- Trial accounts may call only verified destinations, prepend announcements,
  or prohibit unverified caller IDs. Verify the destination before the smoke.
- Present only a provider-registered CLI. Spanish anti-spoofing rules can block
  international-origin calls that present a Spanish CLI, and mobile caller IDs
  are restricted for commercial/customer-service traffic.
- Do not test emergency numbers. Production emergency-service obligations and
  location routing require a provider-specific compliance review.
- Recording consent, retention, and disclosure remain separate from media
  streaming. Use Lucy's recording control channel and consent reference.

## Cost and jurisdiction telemetry

`CpaasCostMetadata` serializes the provider registry key, inbound/outbound
direction, ISO alpha-2 country code, billable seconds, and the
`telephony_cost` component into bounded wire-v1 tags. The existing
`provider_attribution.telephony_cost` field owns the monetary component. No
phone number is an allowed telemetry dimension.
