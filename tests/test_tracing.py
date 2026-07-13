import uuid

from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.observe import Tracer
from lucy.session import VoiceSession
from lucy.testing import InMemoryTraceExporter
from lucy.tracing import Span, TurnSpanTree
from lucy.transport.dev_gateway import LocalGatewaySimulator


def _ids():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lucy-event-{state['n']}"))

    return factory


async def _canned(user_text: str) -> str:
    return "ok: %s" % user_text


def test_turn_span_tree_parents_nodes_under_turn_under_session():
    tree = TurnSpanTree("s1")
    tree.start_session(0)
    turn = tree.turn_span("turn_0", 100, 400)
    node = tree.node_span(turn, "stt", 100, 160)
    tree.end_session(500)

    assert isinstance(node, Span)
    assert tree.session_span.parent_id is None
    assert turn.parent_id == tree.session_span.span_id
    assert node.parent_id == turn.span_id
    assert node.name == "lucy.node.stt"
    assert tree.session_span.ended_at_ms == 500


async def test_one_call_emits_session_turn_node_spans_with_correct_parents():
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporters=[exporter], clock=lambda: 1000, id_factory=_ids())
    gw = LocalGatewaySimulator(booking_happy_path(), ManualClock())
    session = VoiceSession("s1", gw, _canned, tracer=tracer, clock=ManualClock())

    await session.run()
    tracer.flush()

    spans = [e for e in exporter.events if e.type == "span"]
    names = [s.name for s in spans]
    assert "lucy.session" in names
    assert names.count("lucy.turn") == 2

    session_span = next(s for s in spans if s.name == "lucy.session")
    assert session_span.parent_id is None

    turn_spans = [s for s in spans if s.name == "lucy.turn"]
    assert all(t.parent_id == session_span.span_id for t in turn_spans)

    node_spans = [s for s in spans if s.name.startswith("lucy.node.")]
    assert node_spans
    turn_ids = {t.span_id for t in turn_spans}
    assert all(n.parent_id in turn_ids for n in node_spans)
    # the three M0 phases are present
    assert {"lucy.node.stt", "lucy.node.llm", "lucy.node.tts"} <= set(names)
