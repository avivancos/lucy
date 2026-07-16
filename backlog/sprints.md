# Sprint index

Sprints are scope-based batches with a verifiable exit demo, not time boxes.
The folder a card sits in is still its state (see `agent_index.md`); the
`Sprint:` header field maps every card to exactly one sprint. Work sprints in
order unless the dependency notes say a sprint can start early.

| Sprint | Phase | Cards | Exit demo |
| --- | --- | --- | --- |
| S0 Foundations | Docs | 17, 18, 19 | ADRs 0010/0011, telemetry wire spec, Apache-2.0 license shipped (done) |
| S1 Open-core restructure | Packaging | 20, 21, 22, 23, 24, 25, 26, 27 | Quickstart runs with zero keys; pili and lucy-platform extracted; three clean git repos; full suite green |
| S2 Voice runtime core | Runtime | 32, 33, 34, 64 | Scripted call end-to-end: streaming LLM, mid-call MCP tool with filler, real per-turn LatencyWaterfall |
| S3 Conversational correctness | Runtime | 35, 36, 31 | booking_interruption eval green; <800 ms p50 budget asserted with ManualClock; traces visible in `lucy dev` viewer |
| S4 Graph and state | Runtime | 37, 39, 95, 96 | Prebuilt `booking_agent()` passes all six golden scenarios; grounded RAG context reaches the LLM; interrupted heard text survives in graph memory; kill/resume mid-call works |
| S5 Native telephony | Telephony | 97, 40, 41, 42, 43, 44, 45, 100, 101, 102 | Real call softphone -> Asterisk -> Lucy locally; real PSTN call via CPaaS with a Spanish DID |
| S6 Provider ecosystem | Providers | 28, 29, 38, 51, 68 | Quickstart with real provider spec strings; same eval suite green on cascaded and realtime drivers; routed LLM fallback spans are observable |
| S7 Cloud observability seam | Platform | 30, 47*, 48*, 72, 73 | `LUCY_API_KEY` end-to-end: same script, console -> cloud -> dashboard; run identity, tags, and blob presign are wired |
| S8 Launch | Launch | 46, 49, 50, 93 | Public repo installable: `pip install` + quickstart from scratch on a clean machine; launch-base hygiene removed stale API shims |
| S9 Analytics & SRE observability | Platform/Runtime | 52, 54, 55, 56, 57, 66, 67, 94, 98, 99, 103 | Local run serves an analytics-model-v1 rollup at `/analytics`, Grafana renders RED/USE metrics, and the SDK emits full voice costs, talk-time attribution, provider/RAG attribution, and local budget enforcement |
| S10 Agent operations | Process | 61, 62, 63, 65 | CLAUDE.md auto-loads the operating contract; a card moves to done only with recorded reviewer verdicts; the contract test goes red on missing Review evidence; ruff/mypy run in the sanctioned Docker image; clean API builds keep the Docker context lean |
| S11 Platform feed (SDK) | Runtime/Observability | 69, 70, 71 | A simulated call records dual-leg WAVs through LocalGatewaySimulator to a local blob server; `audio_ref` + `cost` + tagged events land in JSONL; the session resumes from Postgres after a process restart |
| S12 Platform core | Platform | 74*, 75*, 76* | Compose brings up platform Postgres and MinIO; a key is minted; replayed fixtures land in Postgres; a second project's key proves tenant isolation |
| S13 Trace explorer, recordings, live ops | Platform | 77*, 78*, 79*, 80*, 81*, 82* | Click a session to see turn/span tree, transcript, synced audio playback, waterfall, FTS search, and live replay updates |
| S14 Money & analytics | Platform | 83*, 58*, 59*, 60*, 84* | Cost Board shows the seven-component split and booked-vs-failed over replayed runs; `/spend?group_by=agent` matches fixture totals; analytics boards render from the semantic API |
| S15 Evals, alerts & hardening | Platform | 85*, 86*, 87*, 88*, 89*, 90*, 91*, 92* | Two eval runs upload from the SDK harness and diff with regression badges; a cost alert fires a webhook; viewer RBAC and RLS block cross-tenant access |

Cards marked `*` live in `lucy-platform/backlog/` (47 ingest v0, 48 dashboard
on real traces, 58 analytics warehouse + ETL, 59 analytics query/semantic API,
60 analytics BI dashboards, and platform cards 74-92), not in this repo.
Platform card 118 is the post-79 recording upload lifecycle hardening dependency
for SDK card 73.

## Dependency notes

- S2 -> S3 -> S4 are strictly sequential (each builds on the previous
  runtime layer).
- Card 95 closes the grounded-context correctness gap in the landed card 37
  default graph and live cascaded session before the prebuilt catalog in card
  39 builds on that path.
- Card 96 makes the live session's heard transcript authoritative for graph
  memory after cancellation or playback interruption. Card 69 persists that
  reconciled state across process and telephony-session boundaries.
- S5 is intentionally parked until the S13 platform demo, then runs 97 -> 40
  -> 41 -> 42, with 43/44/45 scoped from the S5 findings. Card 97 closes the
  graph-to-gateway directive path before real transports depend on it. The
  transports still adapt the control-channel schema defined in S2.
- S6 needs S4 (plugins resolve into the full runtime). Card 51 must wait until
  card 70 lands so Rust/Python golden control-channel fixtures include recording
  messages.
- S7 needs S1 (the `lucy.observe` seam). Card 72 should run before cards 66,
  70, and the platform schema. Card 73 needs card 30, card 71, and platform
  card 118.
- S8 is last and gates on everything shipped in S1-S7 that the launch story
  demos; card 93 closes stale runtime shims before the public ABI freezes.
- S9 open cards (52, 54-57, 66-67, 98, 103) need S1 (the `lucy.observe`
  seam, cards 24/25)
  and can run before the parked telephony lane. Platform cards 58*-60* moved to
  S14 and need card 52 plus the S12/S13 platform substrate.
- Card 98 follows card 66 and adds per-component provider attribution to cost
  events so platform provider spend never guesses across cascaded STT/LLM/TTS.
- Card 99 follows card 52 and the real media-plane VAD work. It adds caller and
  agent speech-duration measures to the analytics model so talk ratio is measured
  from speaker activity rather than inferred from billable audio minutes.
- Decision 53 records the retired rollup proposal and is not executable. Card
  103 follows cards 98 and 99 and is the sole implementation owner. It completes
  the open in-process analytics-model/v1 engine
  for typed turn/session/tool facts, all dimensions and measures, both rollup
  snapshots, percentiles, and every derived metric; it adds no persistence or
  cross-tenant storage.
- S10 is process tooling: it can run at any time, gates nothing in S2-S8, and
  card 62 depends on card 61.
- S11 feeds the platform from the SDK and should run 69 -> 70 -> 71 after card
  72 gives the platform stable run identity and tags.
- S12 runs before the materialized S7 platform cards 47*/48*: ingest and the
  dashboard need tenancy, keys, and recorded replay fixtures.
- S13 is the first platform demo gate and unlocks the parked S5 telephony lane.
- S14 and S15 are post-core platform hardening tracks; ClickHouse stays deferred
  behind card 59's semantic API until measured load requires it.

## Sprint field convention

Card headers carry `**Sprint:** S<N> - <name>` matching this file. The
contract test `tests/test_backlog_contract.py` enforces that every upgraded
pending card names a sprint listed here and that every card listed here
exists in the backlog.
