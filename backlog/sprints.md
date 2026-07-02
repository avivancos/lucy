# Sprint index

Sprints are scope-based batches with a verifiable exit demo, not time boxes.
The folder a card sits in is still its state (see `agent_index.md`); the
`Sprint:` header field maps every card to exactly one sprint. Work sprints in
order unless the dependency notes say a sprint can start early.

| Sprint | Phase | Cards | Exit demo |
| --- | --- | --- | --- |
| S0 Foundations | Docs | 17, 18, 19 | ADRs 0010/0011, telemetry wire spec, Apache-2.0 license shipped (done) |
| S1 Open-core restructure | Packaging | 20, 21, 22, 23, 24, 25, 26, 27 | Quickstart runs with zero keys; pili and lucy-platform extracted; three clean git repos; full suite green |
| S2 Voice runtime core | Runtime | 32, 33, 34 | Scripted call end-to-end: streaming LLM, mid-call MCP tool with filler, real per-turn LatencyWaterfall |
| S3 Conversational correctness | Runtime | 35, 36, 31 | booking_interruption eval green; <800 ms p50 budget asserted with ManualClock; traces visible in `lucy dev` viewer |
| S4 Graph and state | Runtime | 37, 39 | Prebuilt `booking_agent()` passes all six golden scenarios; kill/resume mid-call works |
| S5 Native telephony | Telephony | 40, 41, 42, 43, 44, 45 | Real call softphone -> Asterisk -> Lucy locally; real PSTN call via CPaaS with a Spanish DID |
| S6 Provider ecosystem | Providers | 28, 29, 38, 51 | Quickstart with real provider spec strings; same eval suite green on cascaded and realtime drivers |
| S7 Cloud observability seam | Platform | 30, 47*, 48* | `LUCY_API_KEY` end-to-end: same script, console -> cloud -> dashboard |
| S8 Launch | Launch | 46, 49, 50 | Public repo installable: `pip install` + quickstart from scratch on a clean machine |
| S9 Analytics & SRE observability | Platform/Runtime | 52, 53, 54, 55, 56, 57, 58*, 59*, 60* | Local run serves an analytics-model-v1 rollup at `/analytics` and a Grafana board renders RED/USE metrics from `/metrics/prometheus`; with `LUCY_API_KEY` set, the same events land in the platform warehouse and a cross-run funnel/cost board renders |
| S10 Agent operations | Process | 61, 62 | CLAUDE.md auto-loads the operating contract; a card moves to done only with recorded reviewer verdicts; the contract test goes red on missing Review evidence |

Cards marked `*` live in `lucy-platform/backlog/` (47 ingest v0, 48 dashboard
on real traces, 58 analytics warehouse + ETL, 59 analytics query/semantic API,
60 analytics BI dashboards), not in this repo.

## Dependency notes

- S2 -> S3 -> S4 are strictly sequential (each builds on the previous
  runtime layer).
- S5 can start as soon as card 32 (S2) lands: the transports adapt the
  control-channel schema defined there.
- S6 needs S4 (plugins resolve into the full runtime); card 51 (Rust gateway)
  only needs card 32's schema and can run as a parallel track.
- S7 needs S1 (the `lucy.observe` seam) and can run parallel to S2-S6.
- S8 is last and gates on everything shipped in S1-S7 that the launch story
  demos.
- S9 open cards (52-57) need S1 (the `lucy.observe` seam, cards 24/25) and can
  run parallel to S5-S7; the platform cards 58*-60* need S7 (the cloud seam,
  cards 30/47). S9 is not an S8 launch gate.
- S10 is process tooling: it can run at any time, gates nothing in S2-S8, and
  card 62 depends on card 61.

## Sprint field convention

Card headers carry `**Sprint:** S<N> - <name>` matching this file. The
contract test `tests/test_backlog_contract.py` enforces that every upgraded
pending card names a sprint listed here and that every card listed here
exists in the backlog.
