# Local Asterisk telephony lab

The opt-in `telephony-lab` Compose profile runs a pinned Asterisk 22 instance
and a deterministic ARI caller. It exercises a real PBX, a real Local channel,
and the repository's 8 kHz WAV fixture without requiring a carrier.

## Start and verify

Start only Asterisk and wait for its ARI and PJSIP health checks:

```bash
docker compose --profile telephony-lab up -d --wait asterisk
docker compose --profile telephony-lab ps asterisk
```

Run the deterministic call:

```bash
docker compose --profile telephony-lab run --rm telephony-caller
```

The caller uses authenticated ARI to originate `Local/lab-check@lucy-lab`.
The dialplan answers, plays `booking_caller_8k.wav`, and reports success only
when Asterisk sets `PLAYBACKSTATUS=SUCCESS`. The command exits non-zero on an
ARI error or timeout.

Stop the lab with:

```bash
docker compose --profile telephony-lab stop asterisk
docker compose --profile telephony-lab rm -f asterisk
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
Dial `lucy-audiosocket` to exercise the media-fork route after the Rust
AudioSocket listener from card 41 is running. The RTP range is
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

## Troubleshooting

- Check startup and call logs with
  `docker compose --profile telephony-lab logs asterisk`.
- If Asterisk is unhealthy, verify that ARI port `18088`, SIP port `15060`, and
  RTP ports `10000-10019` are free, or override the host ports in `.env`.
- An ARI `401` means the caller and Asterisk received different
  `LUCY_TELEPHONY_ARI_USER` or `LUCY_TELEPHONY_ARI_PASSWORD` values.
- A softphone that registers but has no audio usually has a blocked UDP RTP
  range or a non-UDP SIP transport.
- `lucy-audiosocket` will end when no gateway is listening on the configured
  target. Use `lab-check` to isolate PBX, ARI, and fixture problems before
  debugging the card 41 adapter.

Protocol details and supported Asterisk paths are tracked in
[`telephony-connectivity.md`](telephony-connectivity.md).
