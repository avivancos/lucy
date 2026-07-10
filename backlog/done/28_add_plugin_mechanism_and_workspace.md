# 28 - Add provider plugin mechanism and workspace split

**Sprint:** S6 - Provider ecosystem
**Epic:** SDK surface
**Estimated effort:** ~8 h
**Depends on:** 26
**State:** done

## Goal

Let providers ship as separately released packages so the core never
inherits provider SDK dependency churn, while spec strings stay the single
resolution syntax. After this card, `"deepgram/nova-3"` in a `VoiceSpec`
resolves through entry points to a plugin factory, `"local"` resolves to
the `lucy.testing` simulators, and both travel one code path.

## Context primer

Read, in this order, before writing anything:

- `agents.md` - project operating rules (English, no mocks, no hardcoded
  provider names or group strings).
- `docs/adr/0010-open-core-split.md` - provider plugins are open SDK; the
  voice provider Protocols and spec models are the frozen public ABI,
  changes governed by SemVer.
- `docs/adr/0003-no-mocks-testing-policy.md` - why the test plugin is a
  real installed package, never a monkeypatched entry point.
- `src/lucy/providers.py` - `Capability`, `ModelInfo`, `ModelRegistry`,
  `default_model_registry()`, `revalidate_model_registry()`; this card
  extends it with spec-string parsing and catalog merge.
- `src/lucy/voice.py` - `SttProvider`/`TtsProvider` Protocols (the ABI you
  freeze) and `LocalSttSimulator`/`LocalTtsSimulator` (what `"local"`
  resolves to).
- `src/lucy/specs.py` - `VoiceSpec.stt_provider`/`tts_provider` are the
  fields that carry spec strings.
- `src/lucy/agent.py` - `VoiceAgent` facade (built in card 26, this card's
  dependency); its provider resolution is the code path you replace.
- `src/lucy/testing/` - public simulators subpackage (built in card 22);
  the fixture plugin factories import from here.
- `pyproject.toml` - current single-package metadata; becomes the uv
  workspace root.
- `Dockerfile.api` - the Compose test image; must install the fixture
  plugin or Compose runs of `tests/test_plugins.py` fail.
- `tests/test_registry_mcp_metrics.py` - house test style for registry
  code: plain pytest, asyncio_mode auto, no mocks.
- `backlog/pending/29_ship_first_provider_plugins.md` - the consumer of
  this ABI; read it to avoid over- or under-building the contract.

## Spec

### Frozen ABI (`src/lucy/voice.py`)

- Decorate `SttProvider` and `TtsProvider` with
  `@typing.runtime_checkable`. Method signatures are UNCHANGED:
  `async def transcribe(self, chunks: List[AudioChunk]) ->
  List[TranscriptEvent]` and `async def synthesize(self, session_id: str,
  text: str) -> List[TtsStreamEvent]`.
- Each Protocol docstring states it is the frozen plugin ABI per ADR 0010
  and that signature changes are SemVer-breaking.

### Spec strings (`src/lucy/providers.py`)

- Module constant `LOCAL_PROVIDER_NAME = "local"`.
- `class InvalidProviderSpecError(ValueError)`.
- `def parse_spec_string(value: str) -> Tuple[str, Optional[str]]` with
  exactly these rules:
  - `"local"` -> `("local", None)`.
  - `"<plugin>/<model>"` with exactly one `/` and both halves non-empty
    -> `(plugin, model)` (example: `"deepgram/nova-3"` ->
    `("deepgram", "nova-3")`).
  - Everything else raises `InvalidProviderSpecError`: empty string,
    whitespace-only, bare names other than `"local"`, `"x/"`, `"/y"`,
    `"a/b/c"`. The message includes the offending value and both valid
    forms.
- Spec strings resolve across `VoiceSpec.stt_provider`/`tts_provider` now;
  future LLM fields (cards 33/38) reuse the same parser unchanged.

### Plugin ABI and lazy loading (`src/lucy/plugins.py`, new)

- Module constant `PLUGIN_ENTRY_POINT_GROUP = "lucy.plugins"`.
- `@dataclass(frozen=True) class LucyPlugin` with fields, in order:
  `name: str`, `version: str`, `capabilities: List[Capability]`,
  `catalog: List[ModelInfo]`,
  `stt_factory: Optional[Callable[[str], SttProvider]] = None`,
  `tts_factory: Optional[Callable[[str], TtsProvider]] = None`,
  `llm_factory: Optional[Callable[[str], object]] = None`,
  `realtime_factory: Optional[Callable[[str], object]] = None`,
  `embedding_factory: Optional[Callable[[str], object]] = None`.
  Factories take the model id (the part after `/`) and return a provider.
  `llm`/`realtime`/`embedding` factories are declared now but resolved by
  cards 33/38; their return Protocols do not exist yet, hence `object`.
- Error hierarchy: `PluginError(Exception)`;
  `PluginNotFoundError(PluginError)` - message lists sorted available
  plugin names plus `"local"`; `PluginCapabilityError(PluginError)` -
  plugin exists but has no factory for the requested capability;
  `PluginAbiError(PluginError)` - entry point object is not a
  `LucyPlugin`, or a factory result fails the runtime-checkable Protocol
  `isinstance` check.
- `class PluginRegistry`:
  - `@classmethod def discover(cls, group: str = PLUGIN_ENTRY_POINT_GROUP)
    -> "PluginRegistry"` - enumerates entry points in the group and stores
    name -> entry point WITHOUT calling `.load()`. Must work on Python
    3.9 (dict-returning `importlib.metadata.entry_points()`) and 3.10+
    (`entry_points(group=...)`); the venv is 3.9, the Docker image 3.12,
    and both must pass.
  - `def names(self) -> List[str]` - sorted discovered names.
  - `def loaded_names(self) -> List[str]` - sorted names already resolved.
  - `def get(self, name: str) -> LucyPlugin` - loads the entry point on
    first call and caches; non-`LucyPlugin` object -> `PluginAbiError`;
    unknown name -> `PluginNotFoundError`.
  - `def load_all(self) -> List[LucyPlugin]` - eagerly loads every
    discovered plugin (the only eager operation; used for catalog merge).
  - `def resolve_stt(self, spec: str) -> SttProvider` and
    `def resolve_tts(self, spec: str) -> TtsProvider` - both delegate to
    ONE private `_resolve(spec, capability)`:
    - `parse_spec_string(spec)` first; parse errors propagate.
    - `"local"` -> `lucy.testing` `LocalSttSimulator()` /
      `LocalTtsSimulator()` by capability.
    - `(plugin, model)` -> `get(plugin)`, pick the factory by capability
      (`Capability.STT` -> `stt_factory`, `Capability.TTS` ->
      `tts_factory`); factory `None` -> `PluginCapabilityError`; call
      `factory(model)`; `isinstance` against the runtime-checkable
      Protocol; failure -> `PluginAbiError`.
- `def load_plugins(group: str = PLUGIN_ENTRY_POINT_GROUP) ->
  PluginRegistry` - the public discovery entry point; returns the lazy
  registry (alias for `PluginRegistry.discover`).
- Import direction: `plugins.py` imports from `providers.py` and
  `voice.py`. `providers.py` must NOT import `plugins.py` at module level
  (use `typing.TYPE_CHECKING` for the `LucyPlugin` annotation) or the
  import cycle breaks the package.

### Catalog merge (`src/lucy/providers.py`)

- `default_model_registry(plugins: Sequence["LucyPlugin"] = ()) ->
  ModelRegistry` - core catalog plus each plugin's `catalog` appended in
  plugin order; `version` stays the core registry date string.
- `class ModelCatalogConflictError(ValueError)` - raised when any
  `(provider, model)` key is duplicated across core and plugin catalogs;
  message names every duplicate as `provider/model`.
- `revalidate_model_registry()` is UNCHANGED and must keep working over
  merged registries, so per-plugin revalidation still works.

### Facade wiring (`src/lucy/agent.py`)

- `VoiceAgent(spec: LucySpec, plugins: Optional[PluginRegistry] = None)`;
  `None` defaults to `load_plugins()`. `spec.voice.stt_provider` and
  `spec.voice.tts_provider` go through `resolve_stt`/`resolve_tts` - the
  ONLY resolution code path. Card 26's "unknown provider string raises a
  clear error" behavior now surfaces `PluginNotFoundError` /
  `InvalidProviderSpecError` listing plugin names plus `"local"`.

### Workspace split (`pyproject.toml`, `packages/`, `Dockerfile.api`)

- Add `[tool.uv.workspace]` with `members = ["packages/*"]` to the root
  `pyproject.toml`. The core keeps dist and import name `lucy` and the
  `src/` layout; `[tool.setuptools.packages.find] where = ["src"]` stays
  so `packages/` never leaks into the core dist.
- Extras such as `lucy[deepgram]` are sugar over plugin dists. Card 28
  declares NO provider extras - the dists ship in card 29; this card only
  proves the mechanism with the fixture plugin.
- New workspace member `packages/lucy-fixture-plugin/`:
  - `pyproject.toml`: name `lucy-fixture-plugin`, version `0.1.0`,
    `requires-python >= 3.9`, `dependencies = ["lucy"]`, setuptools `src/`
    layout, and entry points:
    `[project.entry-points."lucy.plugins"]`
    `fixture = "lucy_fixture_plugin:plugin"` and
    `fixture-broken = "lucy_fixture_plugin.broken:plugin"`.
  - `src/lucy_fixture_plugin/__init__.py`: `def make_stt(model: str) ->
    SttProvider` returning `LocalSttSimulator()` and `def make_tts(model:
    str) -> TtsProvider` returning `LocalTtsSimulator()` (imported from
    `lucy.testing` - real deterministic implementations, the sanctioned
    no-mocks doubles); `plugin = LucyPlugin(name="fixture",
    version="0.1.0", capabilities=[Capability.STT, Capability.TTS],
    catalog=[ModelInfo(provider="fixture", model="echo-1",
    capabilities=[Capability.STT, Capability.TTS],
    recommended_for=["plugin contract tests"], low_latency=True)],
    stt_factory=make_stt, tts_factory=make_tts)`.
  - `src/lucy_fixture_plugin/broken.py`: `class NotAProvider` (no
    `transcribe` method) and `plugin = LucyPlugin(name="fixture-broken",
    version="0.1.0", capabilities=[Capability.STT], catalog=[],
    stt_factory=...)` whose factory returns `NotAProvider()`. It exists
    to exercise `PluginAbiError` against a real installed package.
- Install for tests: `.venv/bin/python -m pip install -e
  packages/lucy-fixture-plugin` locally; in `Dockerfile.api` add
  `COPY packages ./packages` and `RUN pip install --no-cache-dir -e
  packages/lucy-fixture-plugin` after the root install.

## Chips

- [x] **C1 - Freeze the ABI as runtime-checkable Protocols.** Write
  `tests/test_plugins.py` first:
  `test_simulators_satisfy_runtime_checkable_provider_protocols` -
  `isinstance(LocalSttSimulator(), SttProvider)` is true, same for TTS,
  and a plain `object()` fails both checks. Then add `@runtime_checkable`
  and the ABI docstrings in `src/lucy/voice.py`. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py -q` -> all pass.
- [x] **C2 - Spec-string parser.** Tests first in `tests/test_plugins.py`:
  `test_parse_spec_string_accepts_local_and_plugin_model` and
  `test_parse_spec_string_rejects_malformed_values` (every malformed case
  listed in the Spec, asserting the message names the bad value).
  Implement `parse_spec_string`, `LOCAL_PROVIDER_NAME`, and
  `InvalidProviderSpecError` in `src/lucy/providers.py`. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py -q` -> all pass.
- [x] **C3 - LucyPlugin dataclass and error hierarchy.** Test first:
  `test_lucy_plugin_is_frozen_with_optional_factories` (frozen dataclass,
  all five factories default `None`, field order as specced). Implement
  `src/lucy/plugins.py` with `LucyPlugin`, `PLUGIN_ENTRY_POINT_GROUP`,
  and the four error classes; no registry yet. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py -q` -> all pass.
- [x] **C4 - Workspace and fixture plugin package.** Test first:
  `test_fixture_plugin_entry_points_discoverable` - the `lucy.plugins`
  entry-point group contains `fixture` and `fixture-broken`. Create
  `packages/lucy-fixture-plugin/` (pyproject, `__init__.py`, `broken.py`
  exactly as specced), add `[tool.uv.workspace]` to `pyproject.toml`, and
  update `Dockerfile.api`. Verify: `.venv/bin/python -m pip install -e
  packages/lucy-fixture-plugin && .venv/bin/python -m pytest
  tests/test_plugins.py -q` -> install succeeds, all pass.
- [x] **C5 - Lazy PluginRegistry.** Tests first:
  `test_discovery_does_not_import_plugin_module` (pop
  `lucy_fixture_plugin` from `sys.modules`, `load_plugins()`, assert
  `"fixture" in registry.names()` while the module is still absent from
  `sys.modules`; after `get("fixture")` it is present and
  `loaded_names() == ["fixture"]`),
  `test_get_loads_caches_and_returns_same_plugin`, and
  `test_unknown_plugin_raises_with_available_names`. Implement
  `PluginRegistry`, `load_plugins`, and the 3.9/3.10+ entry-points
  accessor in `src/lucy/plugins.py`. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py -q` -> all pass.
- [x] **C6 - One-code-path resolution with ABI enforcement.** Tests first:
  `test_local_and_plugin_specs_resolve_through_one_code_path`
  (`resolve_stt("local")` is a `LocalSttSimulator`;
  `resolve_stt("fixture/echo-1")` satisfies `SttProvider`; same pair for
  TTS), `test_broken_plugin_factory_raises_plugin_abi_error`
  (`resolve_stt("fixture-broken/x")`), and
  `test_missing_capability_factory_raises_capability_error`
  (`resolve_tts("fixture-broken/x")`). Implement
  `resolve_stt`/`resolve_tts`/`_resolve` in `src/lucy/plugins.py`.
  Verify: `.venv/bin/python -m pytest tests/test_plugins.py -q` -> all
  pass.
- [x] **C7 - Catalog merge and revalidation.** Tests first:
  `test_default_registry_merges_plugin_catalogs` (merged registry returns
  `get("fixture", "echo-1")`), `test_catalog_conflict_raises_named_error`
  (an in-test `LucyPlugin` whose catalog duplicates `openai/whisper-1`),
  and `test_revalidation_detects_removed_plugin_model`
  (`revalidate_model_registry(merged, core_only)` reports
  `fixture/echo-1` as removed). Implement the `plugins` parameter and
  `ModelCatalogConflictError` in `src/lucy/providers.py`. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py
  tests/test_registry_mcp_metrics.py -q` -> all pass, no regression.
- [x] **C8 - Wire the VoiceAgent facade.** Tests first:
  `test_voice_agent_runs_turn_with_plugin_spec_strings` (a `LucySpec`
  with `stt_provider="fixture/echo-1"`, `tts_provider="local"` runs a
  turn offline) and `test_voice_agent_unknown_plugin_error_names_available`
  (message lists `fixture` and `local`). Modify `src/lucy/agent.py` to
  accept `plugins` and route all resolution through the registry,
  deleting any card-26 local-only resolution branch. Verify:
  `.venv/bin/python -m pytest tests/test_plugins.py
  tests/test_agent_facade.py -q` -> all pass.
- [x] **C9 - Full suite + bookkeeping.** Run everything, fill
  "Improvements noted", move this card to `done/`. Verify:
  `.venv/bin/python -m pytest -q` -> full suite green (use Docker Compose
  `docker compose run --rm lucy-api pytest` when the daemon is
  available, which also proves the `Dockerfile.api` change).

## Do NOT

- Do not use mocks or mocking frameworks (ADR 0003). The fixture plugin
  is a real installed distribution; never monkeypatch
  `importlib.metadata`, fabricate `EntryPoint` objects in tests, or stub
  factories.
- Do not hardcode provider names, model names, or the entry-point group
  outside `PLUGIN_ENTRY_POINT_GROUP`, `LOCAL_PROVIDER_NAME`, and catalog
  data (agents.md). Resolution logic contains zero provider literals.
- Do not touch files outside the ones this card lists.
- Do not check a Definition of Done box without running its command.
- Do not cross the ADR 0010 boundary: the plugin mechanism is open SDK
  code; no platform or Pili imports. Do not change the Protocol method
  signatures - they are the frozen ABI and changes are SemVer-breaking.
- Do not import `lucy.plugins` from `lucy.providers` at module level;
  the circular import breaks the package (TYPE_CHECKING only).
- Do not call `.load()` on entry points during `discover()`; laziness is
  the contract (only `get()`/`load_all()` import plugin code).
- Do not declare extras for dists that do not exist yet; `lucy[deepgram]`
  and friends arrive with card 29's real plugins.
- Do not move or modify the simulators themselves; card 22 owns
  `lucy.testing`.

## Definition of Done

- [x] `.venv/bin/python -m pytest tests/test_plugins.py
      tests/test_agent_facade.py -q` -> all pass (>= 13 tests in
      `test_plugins.py`)
- [x] `.venv/bin/python -c "from lucy.plugins import load_plugins;
      r = load_plugins(); print(type(r.resolve_stt('local')).__name__,
      type(r.resolve_stt('fixture/echo-1')).__name__)"` -> prints
      `LocalSttSimulator LocalSttSimulator` (one code path, both forms)
- [x] `.venv/bin/python -c "from lucy.plugins import load_plugins;
      from lucy.providers import default_model_registry;
      print(default_model_registry(
      plugins=load_plugins().load_all()).get('fixture', 'echo-1')
      is not None)"` -> prints `True` (merged catalog)
- [x] `grep -n "tool.uv.workspace" pyproject.toml` -> one match
      (workspace declared)
- [x] `.venv/bin/python -m pytest -q` -> full suite green (use Docker
      Compose `docker compose run --rm lucy-api pytest` when the daemon
      is available)
- [x] Post-task audit done; follow-up cards raised for anything noticed

## Failure protocol

If a test fails, a dependency is missing, or the spec turns out wrong: do
NOT check boxes, do NOT force tests green. Leave the card in
`in_progress/`, document what happened under "Improvements noted", and
report. Partial honest work beats fake completion.

## Improvements noted

- Provider spec components containing only whitespace are rejected rather than
  treated as plugin/model identifiers; negative regressions cover both sides.
- Entry-point discovery uses the modern grouped API first and a typed legacy
  dict fallback for Python 3.9 without importing plugin modules.
- Docker lint and formatting were also run over `packages/`, beyond the core
  `src tests` gate, so separately released plugin code follows the same rules.

## Review evidence

- code-reviewer: PASS - lazy loading, one resolver path, cycle direction, and
  catalog conflict handling reviewed with no unresolved P0/P1 findings.
- test-auditor: PASS - clean Docker build and 392 tests pass; 24 plugin tests
  use installed entry points and real simulators without mocks/monkeypatching.
- docs-reviewer: PASS - frozen Protocol docstrings name ADR 0010 and SemVer;
  workspace and entry-point metadata are explicit.
- simplicity-reviewer: PASS - `VoiceAgent` deletes its local-only branches and
  delegates both local and external specs to one registry.
- security-reviewer: PASS - only explicitly installed distributions execute;
  discovery is lazy, no secrets or provider payloads are logged.

Findings disposition:

- [P2][code-reviewer-001] whitespace-only plugin/model components parsed as
  valid - fixed with parser validation and negative tests.
- [P2][test-auditor-001] workspace package was outside static gates - fixed by
  running ruff check/format over `packages/` and recording it in closure.

## Pending human testing

<!-- Only if this card moves to need_human_testing/. -->
