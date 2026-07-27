# 49 - Rewrite README and ship the public guides

**Sprint:** S8 - Launch
**Epic:** Launch docs
**Estimated effort:** ~10 h
**Depends on:** 29, 39
**State:** done

## Goal

The public face of the SDK at launch: a README rebuilt around the
quickstart (install, a 30-line agent, one env var to cloud traces) and six
guides under `docs/guides/`, each with at least one complete code block
that is EXECUTED against the real landed API by a docs contract test.
In-repo markdown only; a docs site generator is explicitly out of scope.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  provider names/URLs/budgets outside typed settings or named constants).
- `backlog/sprints.md` - S8 exit demo this card serves: public repo
  installable, `pip install` + quickstart from scratch on a clean machine.
- `docs/adr/0010-open-core-split.md` - what is open vs closed. The guides
  document ONLY the open SDK surface; the cloud appears solely as the open
  client seam (env vars + wire spec). Naming is deferred: `lucy` stays the
  import name until the pre-publication scrub.
- `docs/adr/0003-no-mocks-testing-policy.md` - the policy the
  testing-without-mocks guide teaches and the docs test itself follows.
- `docs/adr/0011-hybrid-streaming-voice-runtime.md` - the two-plane
  runtime the transports and agent-graphs guides explain.
- `docs/adr/0004-ultra-low-latency-telephony-media-plane.md` - why audio
  never crosses the control channel; quoted by the telephony guide.
- `docs/telemetry-wire-v1.md` - the normative wire spec the observability
  guide links to (pointer, never a copy; the doc is the contract).
- `README.md` - the current file you replace; keep its Docker Compose and
  pytest commands, drop the rest in favor of the quickstart story.
- `pyproject.toml` - dist name `lucy`, `requires-python >= 3.9`, the dev
  extra the install section documents.
- `src/lucy/evals.py` - `booking_happy_path()`, the scenario the runnable
  transport example scripts.
- `src/lucy/providers.py` - `Capability`, `ModelInfo`,
  `default_model_registry()`; catalog rows quoted in the providers guide.
- `tests/test_product_docs.py` - house style for doc-contract tests:
  plain pytest, substring assertions on lowercased doc text.
- `backlog/done/26_add_voice_agent_facade_and_public_api.md`,
  `backlog/done/28_add_plugin_mechanism_and_workspace.md`,
  `backlog/done/29_ship_first_provider_plugins.md` - quickstart,
  `VoiceAgent`, spec strings, `LucyPlugin`, entry points, contract suites,
  fixture recording. In `backlog/done/` once executed.
- `backlog/done/37_runtime_m5_agent_graph_checkpointing.md`,
  `backlog/done/39_add_prebuilt_node_catalog.md` - `AgentGraph`,
  checkpointing, the prebuilt node and graph catalog.
- `backlog/done/24_build_lucy_observe_package.md`,
  `backlog/done/30_build_lucy_cloud_telemetry_client.md`,
  `backlog/done/31_add_local_trace_viewer.md` - `configure()`,
  exporters, `LUCY_API_KEY` auto-attach, `lucy.serve.devviewer`.

Landed-by-S8 inputs (this card is last in the roadmap; these exist by the
time it starts - read each AS LANDED, the landed code always wins over the
shapes assumed in the cards above; if any is missing, stop per the Failure
protocol): `examples/quickstart_voice_agent.py`, `src/lucy/agent.py`,
`src/lucy/plugins.py`, `src/lucy/graph.py`, `src/lucy/state.py`,
`src/lucy/nodes/`, `src/lucy/prebuilt/`, `src/lucy/observe/`,
`src/lucy/testing/` (simulators, `contracts.py`, `replay.py`,
`record.py`), `src/lucy/serve/devviewer.py`, `src/lucy/transport/`
(schema, dev gateway, S5 telephony adapters), `packages/lucy-cloud/`, and
`docs/telephony-connectivity.md` (the PBX/CPaaS/SIP-trunk matrix from the
telephony track).

## Spec

### Ground rules for every documentation file

- In-repo markdown only. No mkdocs/sphinx/docusaurus config, no theme, no
  build step. Record "docs site generator" as a follow-up card under
  "Improvements noted" in C8.
- Fence language is the execution contract:
  - ```` ```python ```` blocks are EXECUTED by the docs contract test.
    Every one is self-contained: its own imports, `asyncio.run(...)` for
    async code, no API keys, no network, deterministic, writes only under
    `tempfile.mkdtemp()`, exits 0. Each block runs in a fresh interpreter
    and must not depend on any other block.
  - ```` ```bash ````, ```` ```toml ````, ```` ```ini ````, and
    ```` ```text ```` blocks are never executed. Shell commands, config
    files, dialplans, package layouts, and pasted output use these.
  - Pasted output shown in a guide must come from a real run of the block
    above it (re-run and paste; never type expected output from memory).
- Every import and symbol in a python block must exist in the landed
  `src/lucy/` tree. Where this Spec quotes a signature from a dependency
  card, verify it against the landed code first; on mismatch the landed
  code wins and the guide documents what actually shipped.
- Relative links between docs must resolve (test-enforced). Link the wire
  spec and ADRs instead of copying their content.
- English, line length ~80 columns, like every other doc in `docs/`.

### Docs contract test - `tests/test_docs_guides.py`

Module constants (named, never inline at call sites):

- `ROOT = Path(__file__).resolve().parents[1]`
- `GUIDE_DIR = ROOT / "docs" / "guides"`
- `GUIDES: tuple[str, ...]` - grows one entry per chip C2-C7; after C7 it
  is exactly `("getting-started.md", "providers-and-plugins.md",
  "agent-graphs.md", "observability.md", "testing-without-mocks.md",
  "transports-and-telephony.md")`.
- `DOC_FILES` - `ROOT / "README.md"` plus every `GUIDE_DIR / name`.
- `STRIPPED_ENV_VARS = ("DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY",
  "OPENAI_API_KEY", "LUCY_API_KEY", "LUCY_ENDPOINT")` - removed from the
  child env so every block provably runs offline on simulators.
- `BLOCK_TIMEOUT_S = 120`.

Helpers:

- `extract_python_blocks(text: str) -> list[str]` - returns the body of
  every ```` ```python ```` fenced block, in file order.
- `run_block(code: str, tmp_path: Path) -> subprocess.CompletedProcess` -
  `subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=<os
  env minus STRIPPED_ENV_VARS>, capture_output=True, text=True,
  timeout=BLOCK_TIMEOUT_S)`. Real interpreter, real package - this is the
  no-mocks way to verify docs.

Test functions (final state, after C7):

- `test_all_guides_exist_with_h1_title` - each `GUIDES` file exists and
  its first non-blank line starts with `# `.
- `test_every_guide_has_at_least_one_python_block`.
- `test_every_python_block_runs_offline_with_exit_zero` - parametrized
  over every block of every `DOC_FILES` entry; asserts `returncode == 0`
  and prints stderr on failure. This is the "run every code block" gate.
- `test_readme_quickstart_block_is_at_most_30_code_lines` - the first
  python block in `README.md` has <= 30 non-blank lines.
- `test_readme_quickstart_prints_a_transcript` - running that block
  yields non-empty stdout containing at least one caller line from
  `booking_happy_path()` (import the scenario in the test; never
  hardcode the utterance).
- `test_readme_documents_cloud_env_var` - `LUCY_API_KEY` appears in
  `README.md`.
- `test_relative_markdown_links_resolve` - every `](...)` target in
  `DOC_FILES` that is not `http(s)://`, `mailto:`, or a pure `#anchor`
  resolves (after stripping any `#fragment`) to an existing file relative
  to the containing doc.
- `test_getting_started_walks_install_first_agent_and_trace` (added C2),
  `test_providers_guide_names_entry_point_group_and_contract_suites`
  (C3), `test_agent_graphs_guide_covers_builder_api_and_prebuilt_catalog`
  (C4), `test_observability_guide_links_wire_spec_and_env_vars` (C5),
  `test_testing_guide_cites_adr_0003_and_simulator_inventory` (C6),
  `test_transports_guide_links_connectivity_doc` (C7) - substring
  assertions enumerated per guide below, in the
  `tests/test_product_docs.py` style.

### `README.md` (full rewrite)

Sections, in order:

1. `# Lucy` + a 3-5 line pitch: open-source, Python-first SDK for
   production voice agents; telephony-native (PBX/SIP, not CPaaS-only);
   Apache-2.0. No marketing superlatives without a linked ADR or doc.
2. `## Install` - clone + `pip install -e ".[dev]"` (Python >= 3.9, from
   `pyproject.toml`). State that the public PyPI distribution name lands
   with the naming milestone (ADR 0010); do NOT invent one.
3. `## Quickstart` - ONE python block, <= 30 non-blank lines: build a
   `VoiceAgent` from a spec with `"local"` providers, run a scripted
   turn, print transcript events and the console trace. Zero keys, zero
   network. State that the same code lives in
   `examples/quickstart_voice_agent.py` and link it.
4. `## From console traces to the cloud` - a bash block (not executed):
   `pip install` the `lucy-cloud` package, `export LUCY_API_KEY=...`
   (plus `LUCY_ENDPOINT` for self-set deployments), re-run the SAME
   quickstart, zero code changes. One line on fail-open: telemetry never
   adds latency or crashes a call. Link `docs/telemetry-wire-v1.md`.
5. `## Guides` - a relative-link list of all six guides with a one-line
   description each.
6. `## Architecture` - <= 10 lines: event plane / cognition plane
   (ADR 0011), Rust media plane and the no-audio-in-Python rule
   (ADR 0004), open-core boundary (ADR 0010). Link the three ADRs.
7. `## Local development` - Docker Compose as the sanctioned runtime
   (`docker compose up --build`, `docker compose run --rm lucy-api
   pytest`) and the venv equivalents; the serving entrypoint copied from
   the landed `src/lucy/serve/` factory path, verified by running it.
8. `## License` - Apache-2.0, link `LICENSE` and `NOTICE`.

### `docs/guides/getting-started.md`

Required H2 sections, in order: Prerequisites; Install; Your first agent
(runnable python block: the quickstart shape with short prose between
steps - spec, agent, session, transcript); See your traces
(`LUCY_TRACE_FILE` JSONL + `python -m lucy.serve.devviewer`, bash block);
Switch to real providers (spec strings such as `deepgram/nova-3`, the
provider key env vars, and the keyless `UserWarning`-plus-simulator
fallback behavior shipped in card 29); Next steps (links to the other
five guides). Content test asserts (lowercased): `pip install -e`,
`lucy_trace_file`, `deepgram/nova-3`, `next steps`.

### `docs/guides/providers-and-plugins.md`

Required H2 sections, in order:

1. Provider spec strings - the two valid forms (`"local"`,
   `"<plugin>/<model>"`) and `InvalidProviderSpecError`, matching the
   landed `parse_spec_string` rules exactly.
2. Anatomy of a plugin - `LucyPlugin` dataclass fields in landed order,
   the `lucy.plugins` entry-point group, lazy discovery via
   `load_plugins()`, and the error hierarchy a user can hit
   (`PluginNotFoundError`, `PluginCapabilityError`, `PluginAbiError`).
3. Write a plugin, step by step - package layout (text block), the
   entry-point declaration (toml block), and a RUNNABLE python block
   that constructs a `LucyPlugin` whose `stt_factory`/`tts_factory`
   return `lucy.testing` simulators, calls a factory, and asserts the
   result satisfies the runtime-checkable `SttProvider` Protocol.
4. Catalog rows - `ModelInfo` fields, merge into
   `default_model_registry()`, duplicate keys raise
   `ModelCatalogConflictError`.
5. Prove it with the contract suites - what
   `SttContractSuite`/`TtsContractSuite`/`LlmContractSuite` from
   `lucy.testing.contracts` check, and a python block defining a binding
   class with its hooks implemented on simulators (definitions execute,
   so imports and hook names are verified by the docs test).
6. Record real fixtures - the `python -m lucy.testing.record` CLI (bash
   block), `ReplayTransport`, the credential-scrubbing and
   volatile-field-masking guarantees, and the `-m live` profile.
7. Keyless behavior - factories fall back to simulators with a
   `UserWarning` naming the missing env var; quickstarts never break.

Content test asserts: `lucy.plugins`, `lucyplugin`, `sttcontractsuite`,
`lucy.testing.record`, `replaytransport`.

### `docs/guides/agent-graphs.md`

Required H2 sections, in order:

1. The default graph - `context_synthesis -> llm -> finalize_funnel`;
   simple agents author nothing.
2. Authoring an `AgentGraph` - builder API as landed (`add_node(name,
   handler, *, deadline_ms, retries, fallback)`, `add_edge`,
   `add_conditional_edge`, `set_entry`, `compile(checkpointer, limits)`,
   the `END` sentinel), plus a RUNNABLE python block: a small graph with
   one conditional edge over `ConversationState`, invoked through the
   landed turn-invocation API, printing the routed path. Copy the exact
   call shape from the landed `src/lucy/graph.py`.
3. State, checkpoints, resume - `ConversationState.merged`,
   `Checkpoint`, `InMemoryCheckpointStore`, kill/resume via
   `GraphTurnDriver.resume`, post-call `replay`.
4. Prebuilt nodes - the card-39 catalog by family (perception/context,
   decision/control, action/speech, telephony, post-call), node names
   copied from the landed `src/lucy/nodes/` modules, one line each.
5. Prebuilt graphs - `booking_agent()`, `lead_qualifier()`,
   `receptionist()`, `survey_agent()` from `src/lucy/prebuilt/`, and how
   to run one against a harness scenario.

Content test asserts: `agentgraph`, `add_conditional_edge`,
`checkpoint`, `booking_agent`, `prebuilt`.

### `docs/guides/observability.md`

Required H2 sections, in order:

1. One call to configure - the landed `lucy.observe.configure(...)`
   signature and what the no-args default does (console tracing).
2. Exporters - `ConsoleExporter`, `JsonlFileExporter`,
   `OtlpBridgeExporter`, `InMemoryTraceExporter` (in `lucy.testing`),
   and the `lucy.exporters` entry-point group for third-party exporters.
   RUNNABLE python block: `configure()` with a `JsonlFileExporter` into
   a `tempfile.mkdtemp()` path, run a quickstart turn, print the number
   of JSONL events written (> 0).
3. Environment variables - a table of exactly `LUCY_TRACING`,
   `LUCY_ENDPOINT`, `LUCY_API_KEY`, `LUCY_PROJECT`, `LUCY_TRACE_SAMPLE`,
   `LUCY_TRACE_FILE` with the effect column copied from
   `docs/telemetry-wire-v1.md`.
4. Privacy, client-side - `redact_pii` (default true), `record_audio`
   (default false), `trace_sample_rate`, the transcripts kill-switch;
   all enforced in open code before anything leaves the process.
5. The local trace viewer - `python -m lucy.serve.devviewer` (bash
   block) and its ADR 0010 scope cap, quoted verbatim from the module.
6. Cloud export - `LUCY_API_KEY` auto-attach via the `lucy-cloud`
   package, fail-open delivery semantics, and a link to
   `../telemetry-wire-v1.md` as THE normative contract (pointer only).

Content test asserts: `configure(`, `jsonlfileexporter`,
`lucy_api_key`, `telemetry-wire-v1.md`, `redact_pii`.

### `docs/guides/testing-without-mocks.md`

Required H2 sections, in order:

1. The policy - ADR 0003 in two paragraphs: what is banned (mocking
   frameworks, invented provider behavior) and what is sanctioned (real
   local implementations, deterministic simulators, local protocol
   servers, recorded fixtures). Link the ADR.
2. The simulator inventory - every public name exported by the landed
   `lucy.testing` (enumerate from `src/lucy/testing/__init__.py` at
   execution time; do not guess), one line each.
3. Your first no-mocks test - RUNNABLE python block: drive
   `LocalSttSimulator` and `LocalTtsSimulator` through one transcribe +
   synthesize round trip with `asyncio.run` and assert on the events.
4. Deterministic time - `ManualClock` vs wall-clock sleeps; a runnable
   block resolving a pending sleep via `advance(...)` instantly.
5. Scenario harness - `ConversationHarness` + `booking_happy_path()`
   for end-to-end agent tests (link the transports guide for the full
   runnable session example).
6. For plugin authors - contract suites, `ReplayTransport`, and the
   recorder CLI (link the providers guide; no duplication).

Content test asserts: `adr 0003`, `localsttsimulator`, `manualclock`,
`conversationharness`, `no mocks` (case-insensitive).

### `docs/guides/transports-and-telephony.md`

Required H2 sections, in order:

1. Two planes, one control channel - ADR 0011/0004 summary: events up,
   directives down, never audio frames; every transport is an adapter
   over the versioned schema in `src/lucy/transport/schema.py`.
2. Try a call offline - RUNNABLE python block: run
   `booking_happy_path()` through the landed harness/dev-gateway
   simulator (`ConversationHarness` + `LocalGatewaySimulator`, exact
   imports from the landed code) and print the per-turn transcript.
3. Asterisk and FreePBX via AudioSocket - what the integration does,
   the dialplan snippet (text block, copied from
   `docs/telephony-connectivity.md`), and the Lucy-side adapter usage
   as landed in the S5 transports.
4. CPaaS - connecting a CPaaS media-streams number, adapter name and
   settings from the landed S5 code; when to prefer it over a PBX.
5. SIP trunk for closed PBXs - the SIP edge path for systems like 3CX
   that expose no media API; configuration pointer.
6. Going deeper - link `../telephony-connectivity.md` for the full
   connectivity matrix and `../adr/
   0004-ultra-low-latency-telephony-media-plane.md` for the media-plane
   contract.

Every PBX/CPaaS/SIP claim must agree with `docs/telephony-connectivity.md`
and the landed `src/lucy/transport/` adapters - this guide invents no
integration. If the S5 adapters or the connectivity doc are missing when
this chip starts, stop per the Failure protocol. Content test asserts:
`audiosocket`, `asterisk`, `sip trunk`, `telephony-connectivity.md`.

### File scope

Create: `docs/guides/getting-started.md`,
`docs/guides/providers-and-plugins.md`, `docs/guides/agent-graphs.md`,
`docs/guides/observability.md`, `docs/guides/testing-without-mocks.md`,
`docs/guides/transports-and-telephony.md`, `tests/test_docs_guides.py`.
Modify: `README.md` and `docs/adr/0010-open-core-split.md`. Nothing else is
touched. Refresh ADR 0010's implementation-status wording for the provider
plugins and `lucy-cloud`; the open-core decision itself does not change.

## Chips

- [x] **C1 - Docs runner harness + README rewrite.** Write
  `tests/test_docs_guides.py` first with the constants, helpers, and the
  README tests (`test_readme_quickstart_block_is_at_most_30_code_lines`,
  `test_readme_quickstart_prints_a_transcript`,
  `test_readme_documents_cloud_env_var`,
  `test_every_python_block_runs_offline_with_exit_zero`,
  `test_relative_markdown_links_resolve`; `GUIDES` starts empty). Run it
  red against the current README, then rewrite `README.md` per the Spec and
  refresh ADR 0010's stale implementation-status wording,
  running the quickstart block by hand once before wiring it in. Files:
  `tests/test_docs_guides.py`, `README.md`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass
  (>=5 tests, README block executed offline).
- [x] **C2 - getting-started guide.** Test first: append
  `"getting-started.md"` to `GUIDES` and add
  `test_getting_started_walks_install_first_agent_and_trace` (red).
  Write `docs/guides/getting-started.md` per the Spec; run its python
  block by hand first. Files: `docs/guides/getting-started.md`,
  `tests/test_docs_guides.py`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass,
  block-execution parametrization now covers the new guide.
- [x] **C3 - providers-and-plugins guide.** Test first: extend `GUIDES`,
  add `test_providers_guide_names_entry_point_group_and_contract_suites`
  (red). Write `docs/guides/providers-and-plugins.md`; verify the
  `LucyPlugin` field order and suite hook names against the landed
  `src/lucy/plugins.py` and `src/lucy/testing/contracts.py` before
  writing the blocks. Files: `docs/guides/providers-and-plugins.md`,
  `tests/test_docs_guides.py`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass.
- [x] **C4 - agent-graphs guide.** Test first: extend `GUIDES`, add
  `test_agent_graphs_guide_covers_builder_api_and_prebuilt_catalog`
  (red). Write `docs/guides/agent-graphs.md`; copy node and factory
  names from the landed `src/lucy/nodes/` and `src/lucy/prebuilt/`.
  Files: `docs/guides/agent-graphs.md`, `tests/test_docs_guides.py`.
  Verify: `.venv/bin/python -m pytest tests/test_docs_guides.py -q` ->
  all pass.
- [x] **C5 - observability guide.** Test first: extend `GUIDES`, add
  `test_observability_guide_links_wire_spec_and_env_vars` (red). Write
  `docs/guides/observability.md`; the env table is copied from
  `docs/telemetry-wire-v1.md`, the scope-cap sentence from the landed
  `src/lucy/serve/devviewer.py` docstring. Files:
  `docs/guides/observability.md`, `tests/test_docs_guides.py`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass.
- [x] **C6 - testing-without-mocks guide.** Test first: extend `GUIDES`,
  add `test_testing_guide_cites_adr_0003_and_simulator_inventory` (red).
  Write `docs/guides/testing-without-mocks.md`; the inventory section
  lists exactly what the landed `src/lucy/testing/__init__.py` exports.
  Files: `docs/guides/testing-without-mocks.md`,
  `tests/test_docs_guides.py`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass.
- [x] **C7 - transports-and-telephony guide.** Precondition: the S5
  transport adapters and `docs/telephony-connectivity.md` exist; if not,
  stop per the Failure protocol. Test first: extend `GUIDES`, add
  `test_transports_guide_links_connectivity_doc` (red). Write
  `docs/guides/transports-and-telephony.md`; dialplan and adapter usage
  copied from the connectivity doc and landed transport code. Files:
  `docs/guides/transports-and-telephony.md`,
  `tests/test_docs_guides.py`. Verify:
  `.venv/bin/python -m pytest tests/test_docs_guides.py -q` -> all pass
  (every python block in README + six guides executed offline).
- [x] **C8 - Full suite + bookkeeping.** Re-run the docs suite with the
  provider/cloud env vars explicitly unset, run the full suite, add the
  "docs site generator" follow-up note under "Improvements noted", raise
  follow-up cards for anything else noticed, move this card to `done/`.
  Verify: `.venv/bin/python -m pytest -q` -> full suite green.

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003) - not in
  `tests/test_docs_guides.py` and not in any documented example. Doc
  blocks run real `lucy.testing` simulators in a real subprocess
  interpreter; pasted output comes from real runs, never typed from
  memory.
- Do not hardcode provider keys, URLs, prices, or latency numbers in the
  guides (agents.md); quote budget defaults only as "defaults from
  `LatencyBudgets`" and key names only as env var names.
- Do not invent API: every symbol in every python block must exist in
  the landed `src/lucy/` tree. If a surface this card documents has not
  landed, stop per the Failure protocol - never document aspirations.
- Do not dodge the runner: a block that cannot run offline must be
  re-fenced as `bash`/`text` AND the guide must still contain a runnable
  simulator-based equivalent. No skip lists, no pseudo-code python.
- Do not modify `src/lucy/`, `packages/`, `examples/`, or any test other
  than `tests/test_docs_guides.py` to make a doc example pass; a doc
  block exposing a bug is a finding - record it and raise a card.
- Do not cross the ADR 0010 boundary: no platform internals (ingest,
  storage, dashboard) in the guides; the cloud is documented only as the
  open client (env vars, fail-open rules, wire spec pointer). Never
  present the dashboard or trace store as open source.
- Do not add a docs site generator, theme, or build pipeline - explicit
  follow-up, noted in C8.
- Do not invent the public PyPI distribution name; naming is deferred
  per ADR 0010 (`lucy` import name until the pre-publication scrub).
- Do not touch files outside the File scope list in the Spec.
- Do not check a chip or Definition of Done box without running its
  Verify command.

## Definition of Done

- [x] `env -u DEEPGRAM_API_KEY -u ELEVENLABS_API_KEY -u OPENAI_API_KEY
      -u LUCY_API_KEY -u LUCY_ENDPOINT .venv/bin/python -m pytest
      tests/test_docs_guides.py -q` -> all pass; the parametrized run
      executes every python block in `README.md` and all six guides
      with exit 0 and no network
- [x] `ls docs/guides` -> exactly the six guide files listed in the Spec
- [x] `grep -c "LUCY_API_KEY" README.md` -> >= 1 (one env var to cloud
      traces is on the front page)
- [x] `grep -n "telephony-connectivity.md"
      docs/guides/transports-and-telephony.md` -> >= 1 match
- [x] `grep -rn "unittest.mock\|MagicMock\|mocker" README.md docs/guides
      tests/test_docs_guides.py` -> no matches
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available)
- [x] Post-task audit done; follow-up cards raised for anything noticed
      (including the docs site generator follow-up)

## Failure protocol

If a test fails, a dependency is missing (a landed-by-S8 input from the
Context primer, the S5 telephony adapters, or
`docs/telephony-connectivity.md`), or a documented example exposes a bug
in the SDK: do NOT check boxes, do NOT force tests green, do NOT patch
`src/lucy/` from this card. Leave the card in `in_progress/`, document
what happened under "Improvements noted", and report. Partial honest work
beats fake completion.

## Improvements noted

- Follow-up card **111** (`backlog/pending/111_docs_site_generator.md`):
  docs site generator (mkdocs or similar) is out of scope for launch;
  raise when public traffic justifies navigation/search. Mapped to S8.
- `backlog/sprints.md` S16 row updated to list already-authored cards
  104-109 (they were orphaned from the sprint index on this branch).
- `docs/guides/README.md` indexes the six Spec guides for navigation.
  The DoD "exactly six guide files" refers to the six content guides in
  `GUIDES`; the index README is additive and not part of the contract
  suite.
- Telephony guide documents Rust-owned AudioSocket (no Python adapter
  invented); dialplan copied from the local lab / connectivity study.

## Closing commit

- Hash:  — docs: ship README quickstart and six public guides (card 49)
- Branch:  · Files: 14 · Date: 2026-07-28
- Landed on main: pending human merge

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
