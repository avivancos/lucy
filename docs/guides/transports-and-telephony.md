# Transports and telephony

Lucy connects to PBXs, CPaaS media streams, and SIP trunks through a
versioned control channel. Audio stays on the media plane.

## Two planes, one control channel

Per [ADR 0011](../adr/0011-hybrid-streaming-voice-runtime.md) and
[ADR 0004](../adr/0004-ultra-low-latency-telephony-media-plane.md):
events flow up, directives flow down, and audio frames never cross into
Python. Every transport is an adapter over the versioned schema in
`lucy.transport.schema`. The Rust media gateway owns the socket and
forks audio to STT/TTS providers; Python sees only control events.

## Try a call offline

Run `booking_happy_path()` through `ConversationHarness`, which wires a
`LocalGatewaySimulator` to a `VoiceSession` on a shared clock:

```python
import asyncio

from lucy.evals import booking_happy_path
from lucy.harness import ConversationHarness
from lucy.transport.dev_gateway import LocalGatewaySimulator

async def responder(user_text: str) -> str:
    return "Sure, about: %s" % user_text

async def main():
    assert LocalGatewaySimulator is not None
    result = await ConversationHarness().run(booking_happy_path(), responder)
    for who, text in result.transcript:
        print("%s: %s" % (who, text))

asyncio.run(main())
```

## Asterisk and FreePBX via AudioSocket

Asterisk (>= 18) and FreePBX integrate through AudioSocket: a TCP TLV
audio fork to the Rust media gateway. Python never speaks AudioSocket —
the Rust sidecar owns the listener and emits control-channel events
only. Dialplan (from the connectivity study / local lab):

```text
exten = lucy-audiosocket,1,Answer()
 same = n,Set(LUCY_CALL_ID=${UUID()})
 same = n,AudioSocket(${LUCY_CALL_ID},gateway:9092)
 same = n,Hangup()
```

Select the adapter with `LUCY_TELEPHONY_TRANSPORT_MODE=audiosocket`
(Compose lab default). Details and softphone registration live in
[`../local-telephony-lab.md`](../local-telephony-lab.md).

## CPaaS

For a fast path to real PSTN, configure `cpaas/telnyx` or
`cpaas/twilio` on `VoiceSpec`. The Rust gateway owns every provider
media frame; Python sees only wire-v1 control events. Prefer CPaaS when
you need a DID quickly without operating a PBX. Settings and smoke
procedure are in [`../cpaas-pstn.md`](../cpaas-pstn.md).

## SIP trunk for closed PBXs

Systems like 3CX, Avaya, and Cisco CUCM expose no usable media API for
AI agents. The universal pattern is a SIP trunk (or extension) to the
managed SIP edge. See the connectivity matrix for the customer-side
recipe; do not invent a per-vendor media API when a SIP trunk already
works.

## Going deeper

- Full PBX / CPaaS / SIP matrix:
  [`../telephony-connectivity.md`](../telephony-connectivity.md)
- Media-plane contract:
  [`../adr/0004-ultra-low-latency-telephony-media-plane.md`](../adr/0004-ultra-low-latency-telephony-media-plane.md)
