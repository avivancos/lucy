# 45 - Rust-native SIP stack research spike

**Sprint:** S5 - Native telephony
**Epic:** Native telephony
**Estimated effort:** ~8 h (HARD time box - see Spec)
**Depends on:** 40
**State:** pending

## Goal

Time-boxed research spike, NOT production code: evaluate Rust-native SIP/RTP
stacks hands-on as the eventual replacement for the Asterisk/FreeSWITCH dev
edge, the role ADR 0004 reserves for the Rust media-gateway sidecar.
Deliverables are knowledge, not features: a comparison matrix, a minimal
feature-gated call-answering prototype if feasible inside the box, and an
ADR recommending adopt/wait per stack. Running out of time with documented
findings is SUCCESS.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  ports/thresholds, Docker Compose is the sanctioned runtime).
- `docs/adr/0001-python-first-rust-hot-paths.md` - Rust is reserved for
  measured hot paths; this spike feeds that measurement gate, it does not
  bypass it.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - the boundary
  a native stack would implement: SIP signaling termination, RTP/RTCP,
  jitter buffer, codec transcode, DTMF. This is the Asterisk/FreeSWITCH
  replacement reservation this spike investigates.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the control-channel
  contract any native gateway must eventually speak; never audio frames to
  Python.
- `docs/adr/0010-open-core-split.md` - the Rust media gateway is OPEN
  (Apache-2.0), so the license column in the matrix is a hard gate.
- `media-gateway-rust/Cargo.toml` and `media-gateway-rust/src/main.rs` -
  the health-check stub the prototype extends behind a feature flag.
- `media-gateway-rust/Dockerfile` - pins `rust:1.82-slim`; all cargo
  commands in this card run in that image (no host toolchain assumed).
- `docker-compose.yml` - the `lucy-media-gateway` service whose default
  build must stay byte-identical in behavior; the Asterisk dev edge added
  by card 40 also lives here at execution time (find its service name and
  auto-answer extension by reading card 40 in `backlog/done/` or
  `backlog/testing/`).
- `src/lucy/metrics.py` - `LatencyWaterfall.transport_ms`, the budget slice
  a native stack must win on (ADR 0004 gate).
- `backlog/sprints.md` - S5 exit demo; note card 51 (Rust gateway, S6) is
  the consumer of this spike's recommendation.
- `tests/test_architecture_adrs.py` - house style for doc contract tests;
  your new ADR and matrix tests follow this pattern.

## Spec

Time box. The estimated effort (~8 h) is a hard box. At the start of every
chip, append `C<N> started <ISO-8601 timestamp>` under "Improvements
noted". When the box expires, stop the current chip, write the literal
marker `NOT REACHED WITHIN TIME BOX: <one-line reason>` into the affected
matrix sections, and jump to C6 (ADR) then C7. C1-C3, C6, and C7 are
mandatory; C4 and C5 are sanctioned skips under the marker rule.

Candidates - exactly these seven rows, no more, no fewer:

1. rvoip (signaling + media stack; entry crate `rvoip`, subcrates such as
   `rvoip-sip-core` - pin exact names on crates.io during C2)
2. rsip (SIP types/parser; crate `rsip`)
3. ezk (signaling; crates `ezk-sip-core`, `ezk-sip-types`, `ezk-sip-ua` -
   pin exact names on crates.io during C2)
4. str0m (sans-IO WebRTC/ICE media; crate `str0m`)
5. webrtc-rs (WebRTC/ICE media; crate `webrtc`)
6. audiopus (Opus bindings; crate `audiopus`)
7. opus (Opus bindings; crate `opus`)

The crate names above are starting points, not facts: C2/C3 pin the exact
crates.io names and versions hands-on, and the matrix records what was
actually evaluated. Every cell comes from a primary source (crates.io,
docs.rs, the project repository) or a hands-on command run during this
card - never from model memory. A cell may read `unknown (<reason>)` only
with a concrete reason. This spike needs outbound network access to
crates.io, docs.rs, and github.com; an outage is a documented finding, not
an excuse to invent data.

`docs/research/rust-sip-stack-matrix.md` (new) with sections `## Method`
(exact commands and lookup dates), `## Matrix`, `## Prototype findings`,
`## Benchmark`, `## Sources` (URLs consulted per candidate). The `## Matrix`
table has exactly these columns:

1. Candidate (project name from the list above)
2. Crates evaluated (exact crates.io names + versions)
3. Scope (signaling | media+ICE | codec)
4. Latest release (version + date)
5. Releases last 12 months (count)
6. License (SPDX id + Apache-2.0 compatibility verdict; GPL/LGPL/AGPL =
   incompatible, flag it)
7. RFC coverage - four marks (yes | partial | no | n/a): INVITE dialogs
   (RFC 3261), REFER transfer (RFC 3515), DTMF telephone-event
   (RFC 2833/4733), early media (183 + PRACK, RFC 3262). Codec rows mark
   the signaling RFCs `n/a`.
8. cargo audit (clean | RUSTSEC ids found)
9. Dependency footprint (direct dep count; system libs required to build)
10. Hands-on note (what compiled/ran in the pinned image; blockers)

cargo audit method, per candidate: inside a reusable container from the
image pinned by `media-gateway-rust/Dockerfile`
(`docker run --name lucy-sip-spike -d rust:1.82-slim sleep infinity`, then
`docker exec lucy-sip-spike cargo install cargo-audit --locked` once),
create a scratch crate in the container's `/tmp` (`cargo new`, `cargo add
<crate>@<version>`, `cargo generate-lockfile`, `cargo audit`) and record
the result. If a build demands system packages (`apt-get install -y
--no-install-recommends pkg-config cmake` is pre-approved), record them in
column 9 - required system deps are an evaluation datum. Scratch crates
stay in the container, never in the repo.

Prototype (C4, only if reached): pick the strongest signaling candidate
from the C2 rows - the choice itself is a finding recorded in
`## Prototype findings`. All changes are feature-gated and additive:

- `media-gateway-rust/Cargo.toml`: feature `sip-spike` listing `dep:`
  entries for every new dependency; ALL new dependencies are
  `optional = true`; `[[example]]` blocks for `sip_spike_callee` and
  `sip_spike_bench` with `required-features = ["sip-spike"]`.
- `media-gateway-rust/src/lib.rs` (new): only
  `#[cfg(feature = "sip-spike")] pub mod sip_spike;`.
- `media-gateway-rust/src/sip_spike.rs` (new), public API fixed:
  - `pub const DEFAULT_BENCH_CALLS: u32 = 50;`
  - `pub enum ResponseClass { Provisional, Final }`
  - `pub enum SpikeError` (variants: `Bind`, `Protocol`, `Timeout`; each
    carrying a `String` detail)
  - `pub struct CallReport { pub answered: bool, pub bye_acked: bool,
    pub setup: std::time::Duration }`
  - `pub async fn run_callee(bind: std::net::SocketAddr, calls: u32)
    -> Result<Vec<CallReport>, SpikeError>` - answers `calls` SIP calls:
    INVITE -> 200 OK with a valid PCMU/8000 SDP answer, consume ACK,
    answer BYE with 200 OK. Sending a handful of G.711 silence RTP packets
    after answer is a stretch goal, not a gate.
  - `pub async fn run_caller(target: std::net::SocketAddr, calls: u32,
    until: ResponseClass) -> Result<Vec<std::time::Duration>, SpikeError>`
    - places `calls` calls, measuring INVITE -> first response of class
    `until` per call, then BYE.
- Examples read config from CLI args or env vars `LUCY_SIP_SPIKE_BIND`,
  `LUCY_SIP_SPIKE_TARGET`, `LUCY_SIP_SPIKE_CALLS`; defaults are named
  constants in `sip_spike.rs` (bind default `127.0.0.1:0` = OS-assigned
  port). No hardcoded ports, hosts, or call counts anywhere else.
- `media-gateway-rust/tests/sip_spike.rs` (new) starts with
  `#![cfg(feature = "sip-spike")]` so the default `cargo test` is
  unaffected.

Benchmark (C5, only if reached): run `sip_spike_bench` with
`until=Final` against (a) the in-process spike callee and (b) the card 40
Asterisk edge's auto-answer extension (if the edge exposes none, fall back
to `until=Provisional` and record that). Paste per-target p50/p95 setup
latency into `## Benchmark` with the caveat that loopback-Docker numbers
are indicative only and do NOT satisfy the ADR 0001/0004 measurement gate.

ADR (C6, mandatory): `docs/adr/00NN-rust-native-sip-stack-evaluation.md`
where `00NN` is the lowest unused number in `docs/adr/` at execution time
(0012 when this card was written; S5 sibling cards may have taken it -
check `ls docs/adr/`). Title `# ADR 00NN - Rust-Native SIP Stack
Evaluation`; sections `## Status` (Accepted), `## Context`, `## Decision`,
`## Consequences` (house style per `tests/test_architecture_adrs.py`). The
Decision section gives one verdict line per candidate (all seven) from the
closed set: Adopt now | Trial in card 51 | Wait (with a concrete revisit
trigger) | Reject (with reason), and links the matrix. Consequences state
what card 51 builds against and reaffirm the ADR 0001 measurement gate.

Doc contract tests in `tests/test_research_sip_spike.py` (new), house
style of `tests/test_architecture_adrs.py`: plain pytest, read files,
assert content. The benchmark/prototype assertions accept EITHER real
findings OR the literal time-box marker, so a sanctioned skip never breaks
the suite.

Files created/modified by this card - the complete list:
`docs/research/rust-sip-stack-matrix.md`,
`docs/adr/00NN-rust-native-sip-stack-evaluation.md`,
`tests/test_research_sip_spike.py`, `media-gateway-rust/Cargo.toml`,
`media-gateway-rust/src/lib.rs`, `media-gateway-rust/src/sip_spike.rs`,
`media-gateway-rust/examples/sip_spike_callee.rs`,
`media-gateway-rust/examples/sip_spike_bench.rs`,
`media-gateway-rust/tests/sip_spike.rs`, and this card file.

## Chips

- [ ] **C1 - Matrix skeleton + doc contract test.** Write
  `tests/test_research_sip_spike.py` first:
  `test_sip_matrix_lists_all_candidates_and_columns` asserts
  `docs/research/rust-sip-stack-matrix.md` exists, has the five sections,
  and the `## Matrix` table names all seven candidates and all ten
  columns. Then create the matrix skeleton with every cell
  `unknown (not yet surveyed)`. Log the time-box start. Files:
  `tests/test_research_sip_spike.py`,
  `docs/research/rust-sip-stack-matrix.md`. Verify:
  `.venv/bin/python -m pytest tests/test_research_sip_spike.py -q` -> all
  pass.
- [ ] **C2 - Survey + cargo audit: signaling candidates.** Test first:
  `test_sip_matrix_signaling_rows_complete` - rows rvoip/rsip/ezk have a
  pinned crate+version, an SPDX license, and an audit cell that is `clean`
  or lists `RUSTSEC-` ids (no `unknown` left in columns 2, 6, 8). Then
  start the `lucy-sip-spike` container, install cargo-audit, and run the
  scratch-crate audit method per candidate; fill rows 1-3 fully from
  primary sources and compile attempts; record commands in `## Method` and
  URLs in `## Sources`. Files: `docs/research/rust-sip-stack-matrix.md`,
  `tests/test_research_sip_spike.py`. Verify:
  `.venv/bin/python -m pytest tests/test_research_sip_spike.py -q` -> all
  pass.
- [ ] **C3 - Survey + cargo audit: media and codec candidates.** Test
  first: `test_sip_matrix_media_and_codec_rows_complete` - same
  completeness rule for str0m/webrtc-rs/audiopus/opus; codec rows mark
  signaling RFC cells `n/a`. Then audit and fill rows 4-7 with the same
  method, recording required system libs (expect cmake/pkg-config for
  Opus bindings) in column 9. Files: `docs/research/rust-sip-stack-matrix.md`,
  `tests/test_research_sip_spike.py`. Verify:
  `.venv/bin/python -m pytest tests/test_research_sip_spike.py -q` -> all
  pass.
- [ ] **C4 - Prototype: feature-gated callee answers one SIP call.**
  Sanctioned skip if the box expires (write the marker into
  `## Prototype findings` and add
  `test_sip_matrix_prototype_findings_present`, which accepts findings or
  the marker). Otherwise, Rust test first in
  `media-gateway-rust/tests/sip_spike.rs`:
  `spike_callee_answers_one_call` - spawn `run_callee` on `127.0.0.1:0`,
  drive INVITE -> 200 OK (assert PCMU in SDP) -> ACK -> BYE -> 200 OK
  over a real UDP socket using the chosen candidate's UAC types (real
  library, real socket - the no-mocks boundary), assert one `CallReport`
  with `answered && bye_acked`. Implement `Cargo.toml` feature + optional
  deps, `src/lib.rs`, `src/sip_spike.rs`,
  `examples/sip_spike_callee.rs`. Record what worked/failed in
  `## Prototype findings`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app
  rust:1.82-slim cargo test --features sip-spike` -> green; AND
  `docker compose build lucy-media-gateway` -> default build still green.
- [ ] **C5 - Latency micro-benchmark vs the Asterisk edge.** Sanctioned
  skip under the marker rule. Test first (both layers):
  `spike_caller_measures_call_setup_latency` (Rust, loopback against
  `run_callee`: returns exactly `calls` durations, all > 0) and
  `test_sip_matrix_benchmark_section_has_numbers_or_timebox_marker`
  (Python: `## Benchmark` carries `p50`/`p95` ms figures per target or
  the marker). Implement `run_caller` + `examples/sip_spike_bench.rs`,
  run it against the spike callee and the card 40 Asterisk edge, paste
  p50/p95 + environment caveat into `## Benchmark`. Files:
  `media-gateway-rust/src/sip_spike.rs`,
  `media-gateway-rust/examples/sip_spike_bench.rs`,
  `media-gateway-rust/tests/sip_spike.rs`,
  `docs/research/rust-sip-stack-matrix.md`,
  `tests/test_research_sip_spike.py`. Verify:
  `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app
  rust:1.82-slim cargo test --features sip-spike` -> green; AND
  `.venv/bin/python -m pytest tests/test_research_sip_spike.py -q` -> all
  pass.
- [ ] **C6 - ADR write-up: adopt/wait per stack (mandatory).** Test first:
  `test_sip_adr_gives_verdict_per_stack` - glob
  `docs/adr/*rust-native-sip-stack-evaluation.md` finds exactly one file
  with the four house sections, one verdict line per candidate (all
  seven, from the closed verdict set), a link to the matrix, and the
  ADR 0001 measurement-gate reaffirmation. Then write the ADR at the
  lowest unused number. Files:
  `docs/adr/00NN-rust-native-sip-stack-evaluation.md`,
  `tests/test_research_sip_spike.py`. Verify:
  `.venv/bin/python -m pytest tests/test_research_sip_spike.py
  tests/test_architecture_adrs.py -q` -> all pass.
- [ ] **C7 - Full suite + bookkeeping.** Remove the audit container
  (`docker rm -f lucy-sip-spike`), confirm the default gateway build is
  untouched, run the whole suite, finish the time-box log and findings
  under "Improvements noted", raise follow-up cards (feed verdicts into
  card 51), move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green; AND
  `docker compose build lucy-media-gateway` -> builds green.

## Do NOT

- Do not use mocks or mocking frameworks - no `mockall`, no fabricated SIP
  responses (ADR 0003). The loopback `run_callee` over a real UDP socket
  and real candidate-library messages is the sanctioned local boundary.
- Do not fill any matrix cell from model memory or training data; every
  cell needs a primary source or a hands-on command logged in `## Method`.
- Do not hardcode ports, hosts, call counts, or the Asterisk edge address;
  use the env vars and named constants in the Spec (agents.md).
- Do not touch `media-gateway-rust/src/main.rs`,
  `media-gateway-rust/Dockerfile`, `docker-compose.yml`, or anything under
  `src/lucy/`; do not add non-optional Cargo dependencies; the default
  (feature-less) build must behave identically.
- Do not touch files outside the complete list in the Spec.
- Do not turn the spike into production code: no control-channel wiring,
  no main.rs integration - that is card 51's track (and ADR 0010 keeps
  the managed SIP edge service closed; the gateway itself stays open).
- Do not vendor or copy source from candidate repos into this Apache-2.0
  tree, and do not add GPL/LGPL/AGPL-licensed crates to the prototype
  (ADR 0010 license gate).
- Do not exceed the time box to polish C4/C5; the marker rule exists so
  honest partial findings keep the suite green.
- Do not check a box without running its Verify command.

## Definition of Done

- [ ] `.venv/bin/python -m pytest tests/test_research_sip_spike.py
      tests/test_architecture_adrs.py tests/test_backlog_contract.py -q`
      -> all pass
- [ ] `docker compose build lucy-media-gateway` -> default build green,
      `src/main.rs` and `Dockerfile` unmodified
- [ ] `docker run --rm -v "$PWD/media-gateway-rust":/app -w /app
      rust:1.82-slim cargo test --features sip-spike` -> green, OR
      `grep "NOT REACHED WITHIN TIME BOX"
      docs/research/rust-sip-stack-matrix.md` -> marker with reason present
- [ ] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon is
      available)
- [ ] Post-task audit done; follow-up cards raised (card 51 inputs at
      minimum)

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

Spike-specific rule: the time box expiring is NOT a failure. Skipping C4
and/or C5 under the marker rule, writing the ADR with `Wait` verdicts for
unproven stacks, and moving the card to `done/` with all run commands
green IS success. The only failure modes here are undocumented gaps,
fabricated matrix cells, or a broken default gateway build.

## Improvements noted

<!-- Fill during execution. Raise a follow-up card per item.
     Time-box log lines go here: `C<N> started <ISO-8601 timestamp>`. -->

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
