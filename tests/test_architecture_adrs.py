from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_python_rust_adr_context_explains_latency_tradeoff():
    adr = read("docs/adr/0001-python-first-rust-hot-paths.md")
    adr_lower = adr.lower()

    assert "product" in adr_lower
    assert "velocity" in adr_lower
    assert "external stt, llm, and tts providers" in adr_lower
    assert "p99" in adr_lower
    assert "media processing" in adr_lower
    assert "high-concurrency streaming" in adr_lower
    assert "rust improves external provider latency" not in adr_lower


def test_python_rust_adr_defines_boundary_and_gate():
    adr = read("docs/adr/0001-python-first-rust-hot-paths.md").lower()

    for python_owned in [
        "control plane",
        "runtime v0",
        "specs",
        "registry",
        "mcp",
        "metrics",
        "rag",
        "evals",
        "crm model",
    ]:
        assert python_owned in adr

    for rust_eligible in [
        "media gateway",
        "webrtc/sip routing",
        "jitter buffers",
        "vad",
        "frame processing",
        "backpressure",
        "high-concurrency sessions",
    ]:
        assert rust_eligible in adr

    assert "instrumentation proves a bottleneck or concurrency limit" in adr
    assert "explicit and observable" in adr


def test_python_rust_adr_is_accepted_and_decision_complete():
    adr = read("docs/adr/0001-python-first-rust-hot-paths.md")

    assert "# ADR 0001 - Python First, Rust For Measured Hot Paths" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr
    assert "Python-first voice-agent framework and FastAPI platform" in adr
    assert "Rust is" in adr
    assert "reserved for performance-sensitive media and streaming sidecars" in adr


def test_nextjs_dashboard_adr_names_stack_and_contract():
    adr = read("docs/adr/0002-nextjs-dashboard.md")

    assert "# ADR 0002 - Next.js Dashboard" in adr
    assert "## Status\n\nAccepted" in adr
    assert "React" in adr
    assert "Next.js App Router" in adr
    assert "TypeScript" in adr
    assert "FastAPI OpenAPI contract" in adr
    assert "source of truth for API contracts" in adr


def test_nextjs_dashboard_adr_defines_realtime_policy():
    adr = read("docs/adr/0002-nextjs-dashboard.md").lower()

    for realtime_surface in [
        "live calls",
        "traces",
        "latency",
        "sentiment",
        "funnel",
        "cost",
    ]:
        assert realtime_surface in adr

    assert "websocket" in adr
    assert "sse" in adr
    assert "visually audited before task closure" in adr


def test_open_core_adr_defines_boundary_license_and_telemetry_seam():
    adr = read("docs/adr/0010-open-core-split.md")
    adr_lower = adr.lower()

    assert "# ADR 0010 - Open-Core Split" in adr
    assert "## Status\n\nAccepted" in adr
    assert (
        "anything that runs inside the\nuser's process is open" in adr
        or "anything that runs inside the user's process is open" in adr.replace("\n", " ")
    )
    assert "stores, aggregates, or compares" in adr.replace("\n", " ")
    assert "apache-2.0" in adr_lower
    assert "lucy.observe" in adr
    assert "lucy.testing" in adr
    assert "lucy.serve" in adr
    assert "telemetry-wire-v1" in adr
    assert "pili" in adr_lower
    assert "trademark" in adr_lower


def test_hybrid_runtime_adr_defines_two_planes_and_control_channel():
    adr = read("docs/adr/0011-hybrid-streaming-voice-runtime.md")
    adr_lower = adr.lower()

    assert "# ADR 0011 - Hybrid Streaming Voice Runtime" in adr
    assert "## Status\n\nAccepted" in adr
    assert "event plane" in adr_lower
    assert "cognition plane" in adr_lower
    assert "voicesession" in adr_lower
    assert "agentgraph" in adr_lower
    assert "graphexecutor" in adr_lower
    assert "turndriver" in adr_lower
    assert "never audio frames" in adr_lower
    assert "checkpointstore" in adr_lower
    assert "speculative" in adr_lower


def test_telemetry_wire_spec_is_normative_and_private_by_default():
    spec = read("docs/telemetry-wire-v1.md")
    spec_lower = spec.lower()

    assert "/v1/events" in spec
    assert "idempotency-key" in spec_lower
    assert "fail-open" in spec_lower
    assert "redact_pii" in spec
    assert "record_audio" in spec
    assert "LUCY_API_KEY" in spec
    assert "additive" in spec_lower


def test_repo_ships_apache_license_and_notice():
    license_text = read("LICENSE")
    notice = read("NOTICE")

    assert "Apache License" in license_text
    assert "Version 2.0" in license_text
    assert "Lucy" in notice
    assert "Trademarks" in notice


def test_no_mocks_policy_is_recorded_in_adr_and_operating_docs():
    adr = read("docs/adr/0003-no-mocks-testing-policy.md").lower()
    normalized_adr = " ".join(adr.split())
    agents = read("agents.md").lower()
    backlog_rules = read("backlog/agent_index.md").lower()

    for text in [adr, agents, backlog_rules]:
        assert "no-mocks project" in text

    for required in [
        "real local implementations",
        "deterministic in-process simulators",
        "local protocol servers",
        "recorded fixtures from real interactions",
        "mocking frameworks",
        "invented provider behavior",
    ]:
        assert required in normalized_adr

    assert "real local behavior boundary" in normalized_adr


def test_nextjs_dashboard_adr_is_decision_complete():
    adr = read("docs/adr/0002-nextjs-dashboard.md")

    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr
    assert "generated typed client" in adr
    assert "realtime channels" in adr
    assert "UI implementation must be visually audited before task closure" in adr


TELEPHONY_MEDIA_PLANE_ADR = (
    "docs/adr/0004-ultra-low-latency-telephony-media-plane.md"
)


def test_telephony_media_plane_adr_is_accepted_and_decision_complete():
    adr = read(TELEPHONY_MEDIA_PLANE_ADR)

    assert "# ADR 0004 - Ultra-Low-Latency Telephony Media Plane" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def _normalized_lower(path: str) -> str:
    return " ".join(read(path).lower().split())


def test_telephony_media_plane_adr_defines_data_vs_control_boundary():
    adr = _normalized_lower(TELEPHONY_MEDIA_PLANE_ADR)

    for control_plane_owned in [
        "control plane",
        "session orchestration",
        "provider selection",
        "mcp",
        "rag",
        "evals",
        "metrics",
    ]:
        assert control_plane_owned in adr

    for data_plane_owned in [
        "data plane",
        "media plane",
        "sip",
        "rtp",
        "jitter buffer",
        "packet loss concealment",
        "vad",
        "endpointing",
        "barge-in",
        "dtmf",
        "backpressure",
        "pcmu",
        "opus",
    ]:
        assert data_plane_owned in adr

    assert "audio never crosses into python per frame" in adr


def test_telephony_media_plane_adr_sets_sidecar_pyo3_and_latency_lever():
    adr = _normalized_lower(TELEPHONY_MEDIA_PLANE_ADR)

    assert "sidecar" in adr
    assert "owns the socket" in adr
    assert "pyo3" in adr
    assert "speech-to-speech" in adr
    assert "streaming end-to-end" in adr
    # The orchestration language is not the primary latency lever.
    assert "language choice is secondary" in adr


def test_telephony_media_plane_adr_refines_0001_and_keeps_measured_gate():
    adr = _normalized_lower(TELEPHONY_MEDIA_PLANE_ADR)

    assert "adr 0001" in adr
    assert "refines adr 0001" in adr
    assert "does not supersede" in adr
    assert "instrumentation proves a bottleneck or concurrency limit" in adr
    assert "transport_ms" in adr
    assert "latencywaterfall" in adr
    assert "explicit and observable" in adr
    assert "named constants" in adr
    assert "agents.md" in adr


SELF_HOSTED_INFERENCE_ADR = "docs/adr/0005-self-hosted-pluggable-inference-layer.md"
LORA_MULTITENANCY_ADR = "docs/adr/0006-multi-tenant-fine-tuning-lora-adapters.md"
CONTEXT_SYNTHESIS_ADR = "docs/adr/0007-on-the-fly-context-synthesis.md"
VOICE_CLONING_ADR = "docs/adr/0008-pluggable-voice-identity-and-cloning.md"
TURN_TAKING_ADR = "docs/adr/0009-turn-taking-and-conversational-fluidity.md"


def test_self_hosted_inference_adr_is_accepted_and_decision_complete():
    adr = read(SELF_HOSTED_INFERENCE_ADR)

    assert "# ADR 0005 - Self-Hosted Pluggable Inference Layer" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def test_self_hosted_inference_adr_defines_pluggable_engine_boundary():
    adr = _normalized_lower(SELF_HOSTED_INFERENCE_ADR)

    for required in [
        "self-hosted",
        "pluggable",
        "engine-agnostic",
        "providers.py",
        "registry",
        "typed settings",
        "named constants",
        "40b",
        "transport_ms",
        "adr 0001",
        "adr 0004",
    ]:
        assert required in adr


def test_lora_multitenancy_adr_is_accepted_and_decision_complete():
    adr = read(LORA_MULTITENANCY_ADR)

    assert "# ADR 0006 - Multi-Tenant Fine-Tuning Via LoRA Adapters" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def test_lora_multitenancy_adr_defines_shared_base_and_isolation():
    adr = _normalized_lower(LORA_MULTITENANCY_ADR)

    for required in [
        "lora",
        "adapter",
        "shared base",
        "per-tenant",
        "hosted tier",
        "dedicated",
        "isolation",
        "registry",
        "40b",
        "adr 0005",
    ]:
        assert required in adr


def test_context_synthesis_adr_is_accepted_and_decision_complete():
    adr = read(CONTEXT_SYNTHESIS_ADR)

    assert "# ADR 0007 - On-The-Fly Context Synthesis" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def test_context_synthesis_adr_is_deadline_bounded_and_framework_free():
    adr = _normalized_lower(CONTEXT_SYNTHESIS_ADR)

    for required in [
        "context synthesis",
        "langchain",
        "no orchestration framework",
        "deadline",
        "rag_ms",
        "graphexecutor",
        "grounding",
        "prompt_context",
        "speculative",
        "retrieval-only",
        "adr 0005",
    ]:
        assert required in adr


def test_voice_cloning_adr_is_accepted_and_decision_complete():
    adr = read(VOICE_CLONING_ADR)

    assert "# ADR 0008 - Pluggable Voice Identity And Cloning" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def test_voice_cloning_adr_defines_pluggable_identity_and_consent():
    adr = _normalized_lower(VOICE_CLONING_ADR)

    for required in [
        "voice identity",
        "voice_id",
        "cloning",
        "enrollment",
        "pluggable",
        "self-hosted",
        "saas",
        "consent",
        "pii",
        "tenant",
        "voicespec",
    ]:
        assert required in adr


def test_turn_taking_adr_is_accepted_and_decision_complete():
    adr = read(TURN_TAKING_ADR)

    assert "# ADR 0009 - Turn-Taking And Conversational Fluidity" in adr
    assert "## Status\n\nAccepted" in adr
    assert "## Context" in adr
    assert "## Decision" in adr
    assert "## Consequences" in adr


def test_turn_taking_adr_defines_fluidity_toolset_and_funnel_link():
    adr = _normalized_lower(TURN_TAKING_ADR)

    for required in [
        "turn-taking",
        "turn lifecycle",
        "endpointing",
        "barge-in",
        "interruption",
        "backchannel",
        "filler",
        "speculative",
        "funnel",
        "latencywaterfall",
        "deadline",
        "voice.py",
    ]:
        assert required in adr
