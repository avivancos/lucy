# Telephony Connectivity Study

Status: living reference for Sprint S5 (cards 40-45). Research verified
2026-06-12 against official docs and source repositories; prices and version
facts carry their citation and date - re-verify before contracting or
pinning versions. Strategy context: ADR 0012 (telephony-native first).

## 1. The strategy in one line

Lucy integrates with the telephony world by **being a standard SIP endpoint
and by speaking each PBX's native audio-fork protocol**, never by building
one-off integrations per PBX vendor.

## 2. Connection matrix by PBX/system

| System | Native audio path | Adapter card | Notes |
| --- | --- | --- | --- |
| Asterisk >= 18 (and FreePBX, Issabel 5/Asterisk 18) | AudioSocket (TCP TLV) | 41 | Universal baseline; 8 kHz via dialplan app |
| Asterisk >= 20.16/22.6 | Media over WebSocket (chan_websocket) | 41 | Modern path: JSON control + server-paced bulk audio |
| Asterisk >= 16.6 (legacy Issabel/16) | ARI externalMedia (RTP unicast) | 41 | Fallback for pre-AudioSocket installs |
| FreeSWITCH | mod_audio_stream (WS, L16) | 43 | Open v1.0.0 (MIT); bidirectional streaming is commercial (v1.0.3) - plan playback via JSON streamAudio |
| 3CX, Avaya, Cisco CUCM, cloud PBXs | SIP trunk/extension to our managed edge | 44 | Universal pattern - no usable vendor media API (see 2.1) |
| CPaaS (Twilio, Telnyx) | Provider media-streams WebSocket | 42 | Fastest path to real PSTN |
| Browser/WebRTC, LiveKit | WS control channel + WebRTC media | later | LiveKit SIP (Apache-2.0) optional bridge |

### 2.1 Why SIP trunk is the only universal path for closed PBXs

- 3CX Call Control API (V20) does stream call audio (PCM 16-bit 8 kHz via
  GET/POST /stream) but requires an 8SC+ Enterprise license and only works
  while the call sits on a Route Point; agent-leg audio is lost
  (3cx.com/docs/call-control-api-endpoints, community threads, 2026-06).
  Practical vendor pattern (Synthflow): register an outbound SIP trunk from
  a cloned approved-provider template pointing at the vendor's SIP edge.
- Cisco CUCM: SIP trunk -> route group -> route list -> route pattern is the
  documented integration used by voice-AI vendors (cisco.com system config
  guide; Telnyx CUBE/CUCM article).
- Conclusion: every PBX can route a call to an external SIP destination;
  vendor media APIs are optional enrichment, never the baseline.

## 3. Protocol facts (verified)

### 3.1 Asterisk AudioSocket

- TCP, TLV framing: 3-byte header (1 byte type + uint16 big-endian length).
  Types: 0x00 hangup, 0x01 UUID (16 raw bytes, sent first by Asterisk),
  0x03 DTMF (1 ASCII byte), 0x10 audio slin 8 kHz (PCM 16-bit mono
  little-endian), 0x11-0x18 slin12..192 kHz, 0xFF error.
  Source: docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket/ and
  res/res_audiosocket.c.
- High sample-rate types shipped 2025-11-20 in 20.17.0/21.12.0/22.7.0/23.1.0
  (asterisk PR #1492). The dialplan app `AudioSocket(uuid,host:port)` is
  hard-locked to 8 kHz; `chan_audiosocket` dial strings accept `/c(slin16)`.
- No auth/encryption in the protocol: isolate at network level.

### 3.2 Asterisk ARI externalMedia

- `POST /ari/channels/externalMedia?app=...&external_host=host:port&format=slin16`
  (since 16.6). `encapsulation=rtp|audiosocket|none`,
  `transport=udp|tcp|websocket`. RTP return address via
  `UNICASTRTP_LOCAL_ADDRESS/PORT` channel vars. Bridge recipe: Stasis ->
  answer -> mixing bridge -> add caller + externalMedia channel.
  Reference app: github.com/asterisk/asterisk-external-media.

### 3.3 Asterisk Media over WebSocket (chan_websocket)

- Since 20.16.0/21.11.0/22.6.0/23.0.0
  (docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/). Binary WS
  frames = media; text frames = control (MEDIA_START, DTMF_END,
  MEDIA_XOFF/XON flow control; commands ANSWER, HANGUP, PAUSE_MEDIA,
  MARK_MEDIA). JSON control format since 20.18.0/22.8.0/23.2.0;
  MARK_MEDIA correlation_id -> MEDIA_MARK_PROCESSED since 22.8.0.
- Killer feature for Lucy: push a whole TTS response and Asterisk re-frames
  and re-times it - server-side pacing plus marks, nearly 1:1 with our
  control-channel design (card 32).

### 3.4 FreeSWITCH mod_audio_stream

- amigniter/mod_audio_stream: MIT, active (last push 2026-01-28). WS/WSS,
  binary L16 PCM (8k default, resample to 8k/16k), JSON metadata text
  frames; invocation `uuid_audio_stream <uuid> start <wss-url> <mix-type>
  <rate> <metadata>` over ESL. Inbound playback via JSON
  `{"type":"streamAudio", ...}` (base64). Full-duplex independent playback
  is the commercial v1.0.3 - do NOT depend on it; the open path is JSON
  playback or uuid_broadcast.
- mod_audio_fork (drachtio/jambonz lineage) is effectively dead as open
  source (canonical repos 404; legacy code no longer compiles against
  current FreeSWITCH - signalwire/freeswitch#2907).
- jambonz (MIT) proves the drachtio+FreeSWITCH+rtpengine edge at scale; its
  listen-verb wire shape (one JSON metadata frame, then binary L16, JSON
  control) is the de-facto pattern and matches our schema design.

### 3.5 Rust-native SIP (for card 45)

- rvoip: MIT, beta (sip stack 0.2.x), single-maintainer, most promising;
  ezk: MIT building blocks; rsip: stale (~2024). Too young for v1 - track
  rvoip, revisit after 1.0. LiveKit SIP (Apache-2.0, Go) is an alternative
  bridge if we adopt the LiveKit ecosystem.

## 4. Managed SIP edge recommendation (card 44)

**Asterisk 22 LTS** (supported to 2029-10) as the managed edge image:

- Universal baseline: AudioSocket 8 kHz (works against every Asterisk >= 18
  and is trivial to parse in the Rust gateway).
- Modern path: Media over WebSocket (>= 22.6) - JSON control + pacing.
- Legacy fallback: ARI externalMedia RTP (covers Asterisk 16 Issabel).
- Customer-side recipes: FreePBX via extensions_custom.conf + Custom
  Destination -> Inbound Route; 3CX via generic/cloned SIP trunk template;
  CUCM via route pattern.
- FreeSWITCH stays the adapter target for customers who already run it; we
  do not operate FreeSWITCH as our own edge (bidirectional fork is
  commercial; Asterisk's WS media is license-clean and sufficient).
- Scale-out later: Kamailio/OpenSIPS + rtpengine in front when multi-tenant
  volume demands it; Rust-native edge per card 45 findings.

## 5. Local no-mocks test lab (card 40)

- Docker image: `andrius/asterisk` pinned to an Asterisk 22.x tag
  (actively maintained, verified 2026-06; no official upstream image).
- Minimal config: pjsip endpoint (ulaw+slin16) + extensions.conf routing a
  test exten to `Answer()` -> `AudioSocket(uuid, gateway:9092)`.
- CI caller: **ARI originate of a Local channel** (pure HTTP, no SIP stack
  in CI, deterministic event-based asserts, can Playback() a known WAV into
  AudioSocket) as primary; one SIPp UAC scenario with pcap RTP as ingress
  smoke test; voip_patrol (Docker image jchavanton/voip_patrol) for
  scenario tests with WAV playback, RFC2833 DTMF, and MOS assertions;
  pjsua/baresip as recording far end for "did the bot actually speak".
- Softphones for manual testing: Zoiper/Linphone/MicroSIP registered
  against the local Asterisk - zero provider needed for the full SIP/RTP
  loop; only a real PSTN hop requires section 6.

## 6. Where to register for real PSTN testing (Spain)

Prices as of mid-2026 research; verify before contracting.

| Provider | Self-signup | Why | Cost anchor |
| --- | --- | --- | --- |
| **Telnyx** (CPaaS, recommended) | yes ($5 trial credit) | Bidirectional media streaming with L16 16 kHz explicitly for AI agents (no mulaw transcode); ES DID $1/mo; inbound from $0.0032/min; startup program $10k credits | KYC for +34 DID ~72 h |
| **Twilio** (CPaaS, day-1 demo) | yes | Fastest first streamed call: outbound to your verified mobile needs no Spanish DID/KYC; Media Streams is mulaw 8 kHz ($0.004/min) | ES DID $2/mo + regulatory bundle |
| **Netelip** (Spanish trunk #1) | yes, 30-day free trial | Traditional Spanish operator with online signup; geographic DID from EUR 1.95/mo; registration or IP auth | scanned ID required |
| **Zadarma** (Spanish trunk #2) | yes, instant prepaid | EUR 3.40/mo DID, 10 channels, free inbound, <24 h activation reported; cheap second route for A/B carrier behavior | KYC docs required |
| voz.com / GoTrunk / DIDWW / DIDlogic | yes | Alternatives: voz.com EUR 9/mo 10-channel trunk incl. number (EUR 0.014/min fixed); DIDWW digest+IP auth, API-driven | - |
| Enreach (Telsome), Gamma (VozTelecom), Sarenet, Megacall | no (sales/quote) | Traditional business operators - relevant as CUSTOMER trunks, not for our testing | - |

Regulatory notes (Spain):

- +34 geographic numbers require KYC: ID/passport or company cert + CIF,
  proof of address dated <3 months; provincial numbers need an address in
  the matching area code (Twilio/Telnyx regulatory pages, mid-2026).
- Orden TDF/149/2025 (BOE-A-2025-2870): operators block empty/unassigned
  CLI; international-origin calls presenting +34 CLI are blocked (from
  2025-06); mobile CLIs (6xx/7yx) banned for commercial/customer-service
  calls - present a registered geographic or 800/900 number routed
  domestically. Penalties up to EUR 2M.
- STIR/SHAKEN: not deployed in Spain (mid-2026); CLI enforcement is
  network-level per the Orden above.
- 112 emergency access obligations apply to voice service providers (LGT
  11/2022); CPaaS providers surface this as a per-number fee (e.g. Telnyx
  $1.50/mo).

## 7. Open uncertainties (carry into card execution)

- Exact Asterisk point release that added externalMedia audiosocket+tcp
  encapsulation (works on 22; introducing release uncertain).
- mod_audio_stream commercial edition thresholds; drachtio-freeswitch-mrf
  current module list.
- Issabel 5 Asterisk 20 support claims (conflicting sources) - assume 16/18.
- Twilio trial model is mid-transition (dollar credit vs product-specific
  free units) - check at signup.
- voip_patrol license file absent upstream - verify before bundling.
