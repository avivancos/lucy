# ADR 0012 - Telephony-Native First Connectivity

## Status

Accepted

## Context

Comparable voice frameworks assume CPaaS or WebRTC entry points and leave
the installed base of business PBXs (Asterisk/FreePBX/Issabel, FreeSWITCH,
3CX, Avaya, Cisco) unreachable without bespoke work. That installed base -
dominant among the SMEs Lucy targets - is reachable through two stable
mechanisms: each PBX's native audio-fork protocol, and plain SIP routing.
Vendor media APIs are licensed, version-gated, and lossy (verified for 3CX
in `docs/telephony-connectivity.md`), so they cannot be the baseline.

## Decision

Native telephony connectivity is a first-class pillar of the SDK, built on
one principle: **Lucy is a standard SIP endpoint and speaks each PBX's
native audio-fork protocol; it never builds per-vendor one-off
integrations.**

Connection tiers, in delivery order (cards 40-45, Sprint S5):

1. **P0 - local lab**: in-process gateway simulator (card 32) plus a real
   Asterisk in Docker Compose with scripted callers - the no-mocks
   integration substrate (card 40).
2. **P1 - CPaaS**: Twilio/Telnyx media-streams adapters for immediate real
   PSTN reach (card 42).
3. **P2 - PBX audio forks**: Asterisk adapter speaking AudioSocket
   (universal baseline, >= 18), Media over WebSocket (modern path,
   >= 20.16/22.6), and ARI externalMedia RTP (legacy fallback, >= 16.6)
   (card 41); FreeSWITCH adapter speaking mod_audio_stream's open protocol
   (card 43).
4. **P3 - managed SIP edge**: an Asterisk 22 LTS edge we operate so closed
   PBXs (3CX, Avaya, CUCM, cloud PBXs) connect by pointing a SIP
   trunk/extension at us (card 44). The blueprint is open; the operated
   service is platform product (ADR 0010).
5. **P4 - Rust-native SIP**: deferred until volume justifies it; tracked
   via a time-boxed spike (card 45; rvoip is the leading candidate, still
   beta).

All transports are adapters over the card 32 control-channel schema: audio
flows between the media plane and providers, only events and directives
reach Python (ADR 0004). `VoiceSpec.transport` resolves transports the same
way provider spec strings resolve models. Telephony costs land in the
existing `telephony_cost` component and the media plane reports
`transport_ms`, so the latency and cost advantage of direct SIP over CPaaS
is measurable with our own telemetry.

## Consequences

- The control-channel schema (card 32) must land before any transport
  adapter; the Rust gateway and every adapter are conformance-tested
  against the same golden fixtures (card 51).
- The managed edge gives the platform a commercial service tier while the
  SDK keeps a self-host path with the same open blueprint.
- Spanish market regulatory constraints (KYC for +34 DIDs, Orden
  TDF/149/2025 CLI rules) are documented in
  `docs/telephony-connectivity.md` and must be reflected in outbound
  call features (CLI selection is configuration, never hardcoded).
- `docs/telephony-connectivity.md` is the living reference for protocol
  facts, provider registrations, and open uncertainties; cards cite it
  instead of duplicating research.
