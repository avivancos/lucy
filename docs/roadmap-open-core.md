# Open-Core Roadmap

The sprint index in `backlog/sprints.md` is the operational source of truth
(cards, exit demos, dependencies). This roadmap adds the narrative arc and
the launch gates. Decisions: ADR 0010 (open-core split), ADR 0011 (hybrid
runtime), ADR 0012 (telephony-native first).

## Arc

1. **Separate the products** (S1). One repo becomes three: the SDK (public
   to be), the platform (closed), and Pili (vertical). The telemetry seam
   (`lucy.observe` + wire spec) is the boundary that lets them evolve
   independently.
2. **Make the runtime real** (S2-S4). The hybrid two-plane runtime grows
   from walking skeleton to LangGraph-parity graphs with checkpointing,
   with conversational correctness (interruptions, speculation, budgets)
   proven by deterministic no-mocks tests.
3. **Own telephony** (S5). The differentiator: a real Asterisk lab in CI,
   native PBX adapters, CPaaS reach, and a managed SIP edge blueprint -
   per ADR 0012 tiers.
4. **Open the ecosystem** (S6). Plugin mechanism, the first provider trio,
   speech-to-speech parity, and the Rust gateway speaking the control
   schema.
5. **Monetize observability** (S7). The open `lucy-cloud` client against
   the closed ingest + dashboard: one env var from console traces to SaaS.
6. **Launch** (S8). Scrub, rename, license headers, docs, public CI, PyPI.

## Launch gates (S8 cannot start until)

- Quickstart runs offline on simulators in <30 lines (S1, card 26).
- A real call flows: softphone -> Asterisk -> Lucy locally, and PSTN via
  CPaaS with a Spanish DID (S5 exit demo).
- The same eval suite passes on cascaded and realtime drivers (S6).
- `LUCY_API_KEY` end-to-end demo works (S7).
- Operating docs moved private; naming decided; SPDX + TRADEMARKS in place
  (card 46).

## Standing rules

- Every sprint's exit demo is verifiable by command, not by narrative.
- Backlog cards follow the junior-agent standard enforced by
  `tests/test_backlog_contract.py`.
- Research lives in cited reference docs (e.g.
  `docs/telephony-connectivity.md`); cards point at them.
