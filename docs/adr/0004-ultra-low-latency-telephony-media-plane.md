# ADR 0004 - Ultra-Low-Latency Telephony Media Plane

## Status

Accepted

## Context

Lucy is a Python-first voice-agent framework. ADR 0001 (Python First, Rust For
Measured Hot Paths) already established that Python owns the control plane and that
Rust is reserved for performance-sensitive media and streaming sidecars after
instrumentation proves a bottleneck or concurrency limit. ADR 0001 leaves the
Python/Rust boundary deliberately coarse.

Telephony (SIP/PSTN) is the first transport Lucy optimizes for, and it is the
hardest real-time constraint in the stack. The gateway must own a UDP/RTP socket,
pace 8 kHz G.711 frames every 10-20 ms, absorb network jitter, and react to the
caller inside a single turn. A turn's wall-clock latency is still dominated by
network round-trips and external STT, LLM, and TTS providers, but the media plane
owns the `transport_ms` slice of the `LatencyWaterfall` (see
`src/lucy/metrics.py`) and the p99 behavior under high-concurrency sessions.

The media gateway today is a health-check stub. This ADR fixes the boundary,
ownership, and gate for the telephony media plane so later implementation has an
explicit contract. It refines ADR 0001 for the telephony hot path; it does not
supersede it.

## Decision

The boundary is data plane versus control plane, not "move slow code to Rust".

Rust owns the telephony data plane as a sidecar that owns the socket: SIP signaling
termination, RTP/RTCP transport, jitter buffer and packet loss concealment, codec
transcode (PCMU/PCMA/G.711 to and from PCM and Opus), per-frame VAD and
endpointing, barge-in detection, DTMF detection, audio fan-out to and from provider
STT and TTS streams, and backpressure. Audio never crosses into Python per frame.

Python owns the control plane: session orchestration, provider selection, MCP, RAG,
evals, metrics, the CRM model, and the dashboard contract.

The two planes communicate over a coarse-grained, low-frequency control channel
(session start and stop, transcript events, turn and latency events, control
commands). The control channel must never carry a per-audio-frame round-trip to
Python, and the boundary must remain explicit and observable.

Sidecar versus PyO3: for telephony the default is a Rust sidecar that owns the
UDP/RTP socket, because the hot path must never round-trip to Python. PyO3
in-process bindings are reserved for CPU-bound functions that must run inside a
Python turn loop (for example a resampler), where a network hop would add latency.

The primary latency lever is architectural, not the orchestration language: native
speech-to-speech realtime models (such as gpt-realtime and gemini-live) that
collapse the STT to LLM to TTS chain, combined with streaming end-to-end. Language
choice is secondary to this; the registry's `low_latency` and `realtime` flags drive
the speech-to-speech-first default.

## Consequences

- The media gateway sidecar grows from a health-check stub to own the telephony
  data-plane responsibilities above.
- A typed Python-to-Rust control contract must be defined and kept explicit and
  observable, extending the boundary from ADR 0001.
- Deepening Rust is gated on measurement: the `transport_ms` field of the
  `LatencyWaterfall` and p99 concurrency measurements, consistent with ADR 0001's
  "instrumentation proves a bottleneck or concurrency limit" gate. Rust is not
  expected to reduce external provider latency.
- Latency budgets and thresholds live in typed settings or named constants per the
  operating rules in agents.md, never hardcoded in code or in this ADR.
