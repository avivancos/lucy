# Local Asterisk telephony lab

The opt-in `telephony-lab` Compose profile runs a pinned Asterisk 22 instance
and a deterministic ARI caller. It exercises a real PBX, a real Local channel,
and the repository's 8 kHz WAV fixture without requiring a carrier.

## Start and verify

Build and start Lucy, the Rust gateway, and Asterisk, then wait for the PBX
health check. First generate a random control-channel token with
`openssl rand -hex 32` and assign it to `LUCY_GATEWAY_CONTROL_TOKEN` in the
gitignored `.env` file. The gateway fails closed when the token is absent.

```bash
docker compose --profile telephony-lab up -d --wait --build lucy-api lucy-media-gateway asterisk
docker compose --profile telephony-lab ps lucy-api lucy-media-gateway asterisk
```

Run the deterministic call:

```bash
docker compose --profile telephony-lab run --rm telephony-caller
```

The default caller uses authenticated ARI to bridge
`Local/lucy-audiosocket@lucy-lab` to `play-fixture`. Asterisk sends the
checked-in WAV through its real AudioSocket application to the Rust gateway.
The command succeeds only after gateway health counters prove that a new
session started and completed, audio bytes stayed in Rust, and both
STT control events and playback progress reached Lucy's control channel. It also
requires nonzero outbound PCM, proving that Lucy's response reached Asterisk.
Expected output begins with `Lucy AudioSocket adapter completed`.

To isolate the PBX, ARI, and WAV playback from the adapter, retain the card 40
diagnostic path:

```bash
LUCY_TELEPHONY_TEST_EXTENSION=lab-check \
  docker compose --profile telephony-lab run --rm telephony-caller
```

Both commands exit non-zero on an ARI, gateway, protocol, or deadline failure.

Stop the lab with:

```bash
docker compose --profile telephony-lab stop asterisk lucy-media-gateway
docker compose --profile telephony-lab rm -f asterisk lucy-media-gateway
```

The profile is never started by the default `docker compose up` command.

## Register a softphone

The safe local defaults are listed in `.env.example`. Override them in the
gitignored `.env` file when needed.

| Setting | Local default |
| --- | --- |
| SIP server | `127.0.0.1` |
| Transport | UDP |
| Port | `15060` |
| User | `lucy-lab` |
| Password | `lucy-lab-only` |

Register the account, then dial `lab-check` to hear the same checked-in WAV.
Dial `lucy-audiosocket` to exercise the live media-fork route. The RTP range is
`10000-10019/udp`; all published lab ports bind to localhost.

Inspect registrations and the endpoint from the real PBX:

```bash
docker compose --profile telephony-lab exec asterisk \
  asterisk -rx "pjsip show contacts"
docker compose --profile telephony-lab exec asterisk \
  asterisk -rx "pjsip show endpoint lucy-lab"
```

## Media boundary

Asterisk owns SIP/RTP and forks telephony media directly to the Rust gateway at
the host and port configured by `LUCY_TELEPHONY_AUDIO_SOCKET_HOST` and
`LUCY_TELEPHONY_AUDIO_SOCKET_PORT`. Raw audio frames never enter Python. Python
receives only the versioned control-channel events produced by the gateway.

AudioSocket is intentionally reachable only on the Compose network. It has no
built-in authentication or encryption, so do not publish that listener to a
public interface.

The Rust adapter accepts Asterisk's real-world TCP close as a remote hangup in
addition to the protocol hangup frame. The local recorded media provider turns
the checked-in PCM fixture into VAD/STT events and streams recorded PCM back for
`tts.speak`. All three transports share a 32-clause ordered playback queue;
`flush` is preserved when the production request reaches ElevenLabs, and
`tts.cancel` can stop one clause or clear the active and pending queue. Downstream
`dtmf.send` and `session.end` directives are written back as AudioSocket frames;
AudioSocket transfer is not supported. Production provider backends replace the
fixture behind the same Rust-owned media-plane boundary. Media over WebSocket
supports binary playback, `FLUSH_MEDIA`, `HANGUP`, flow control, and correlated
media marks. This adapter rejects outbound `dtmf.send` and `transfer`
directives. The ARI fallback is the only mode in this card that executes DTMF
and transfer through authenticated ARI. It creates a mixing bridge and
external-media channel, keeps RTP bidirectional for the life of the call,
forwards DTMF and hangup events, executes playback/cancel/control directives,
and reports measured packet loss and jitter.

## Use the production media backend

Set `LUCY_GATEWAY_MEDIA_BACKEND=deepgram_elevenlabs` to stream caller PCM from
the Rust gateway directly to Deepgram and stream ElevenLabs PCM directly back
to the selected Asterisk transport. Configure the provider URLs, model IDs,
ElevenLabs voice ID, PCM format/sample rate, endpointing, framing, and bounded
connect/idle timeouts with the `LUCY_GATEWAY_DEEPGRAM_*`,
`LUCY_GATEWAY_ELEVENLABS_*`, and `LUCY_GATEWAY_PROVIDER_*` settings listed in
`.env.example`. `LUCY_GATEWAY_ELEVENLABS_PLAYBACK_FRAME_MS` controls the PCM
packet duration used for paced AudioSocket, Media WebSocket, and RTP playback;
the default is 20 ms.

Provider credentials belong only in the gitignored `.env` file or deployment
secret store. They are consumed by the Rust gateway, redacted from debug
output, and never forwarded to Python or serialized onto the control channel.
Provider base URLs cannot contain credentials, query parameters, or fragments.
The default `fixture` backend remains the deterministic local-lab path and does
not require provider credentials.

AudioSocket reports measured PCM arrival jitter with `rtt_ms=0` because its TCP
protocol exposes no media-plane ping. Media WebSocket measures RTT with a
ping/pong on the Asterisk media socket itself; the gateway never substitutes
control-channel RTT. ARI reports RTP jitter and packet loss, buffers a bounded
reordering window, and emits deterministic PCM16 silence when that window
proves a packet was lost. Its outbound RTP stream owns a stable gateway SSRC
that is distinct from the source-locked inbound Asterisk SSRC.

## Select an adapter mode

`LUCY_TELEPHONY_TRANSPORT_MODE` accepts `audiosocket`, `media_websocket`, or
`ari_external_media`. AudioSocket remains the Compose lab default. Media over
WebSocket binds to `LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND` and reuses the same
allowlist, session cap, media backend, and handshake/idle deadlines as the
AudioSocket listener.

The checked-in Asterisk dialplan and `telephony-caller` smoke exercise only the
AudioSocket baseline. Changing `LUCY_TELEPHONY_TRANSPORT_MODE` does not rewire
that lab call. Media WebSocket and ARI externalMedia are production adapter
paths covered by real local protocol servers and sockets in the Rust suite;
deployments must connect their own Asterisk media-WebSocket channel or active
ARI caller channel before selecting those modes.

The Rust gateway and Python control endpoint share
`LUCY_GATEWAY_CONTROL_TOKEN`. The gateway sends it as a Bearer credential and
Python rejects missing or incorrect credentials before accepting the WebSocket.
No token value is committed and the local API port binds to loopback. The
`LUCY_GATEWAY_ALLOW_INSECURE_CONTROL_WS=true` override is only for the isolated
local Compose network; production must inject a secret token and use `wss://`
for non-loopback control traffic.

ARI mode is a per-channel fallback. It requires an active caller channel ID,
an authenticated ARI HTTP endpoint, an ARI events WebSocket, and routable RTP
bind/advertised addresses. `.env.example` lists every required
`LUCY_ASTERISK_ARI_*` and `LUCY_GATEWAY_ARI_RTP_*` setting. Replace the example
caller ID and advertised address before selecting this mode. Plain `http` and
`ws` are allowed only for the isolated local lab; production deployments must
terminate TLS and keep ARI credentials out of URLs and logs.

`LUCY_GATEWAY_ARI_RTP_ALLOWED_CIDRS` is enforced before the first RTP packet can
lock the source address or SSRC. The gateway also requires the packet source IP
to match an address resolved from the configured ARI origin. The default covers
loopback and RFC1918 Docker networks for the local lab. Production deployments
should narrow it to the PBX media address or subnet; packets that fail either
check are rejected and cannot become the playback destination.

## Troubleshooting

- Check startup and call logs with
  `docker compose --profile telephony-lab logs asterisk`.
- If Asterisk is unhealthy, verify that ARI port `18088`, SIP port `15060`, and
  RTP ports `10000-10019` are free, or override the host ports in `.env`.
- The startup command also publishes the API and gateway health endpoints on
  ports `8000` and `8081`. Override `LUCY_API_HOST_PORT` and
  `LUCY_GATEWAY_HEALTH_HOST_PORT` in `.env` when either port is occupied.
- An ARI `401` means the caller and Asterisk received different
  `LUCY_TELEPHONY_ARI_USER` or `LUCY_TELEPHONY_ARI_PASSWORD` values.
- A softphone that registers but has no audio usually has a blocked UDP RTP
  range or a non-UDP SIP transport.
- `lucy-audiosocket` will end when no gateway is listening on the configured
  target. Use `lab-check` to isolate PBX, ARI, and fixture problems before
  debugging the card 41 adapter.
- If the adapter smoke reaches its deadline, inspect
  `http://127.0.0.1:8081/health` and the `asterisk_sessions_failed` counter,
  then read `docker compose logs asterisk lucy-media-gateway lucy-api`.

Protocol details and supported Asterisk paths are tracked in
[`telephony-connectivity.md`](telephony-connectivity.md).
