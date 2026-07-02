from lucy.clock import ManualClock
from lucy.evals import booking_happy_path
from lucy.transport.dev_gateway import LocalGatewaySimulator
from lucy.transport.schema import (
    Envelope,
    SessionEnded,
    SessionStarted,
    SttFinal,
    SttPartial,
    TtsPlayback,
    TtsSpeak,
    TtsStreamEnd,
)


async def _drive(gw, response="Happy to help"):
    """Consume the gateway, answering each SttFinal with a TtsSpeak followed by
    the per-turn stream-end barrier (exactly as a VoiceSession does, card 64)."""
    events = []
    async for event in gw.events():
        events.append(event)
        if isinstance(event.payload, SttFinal):
            await gw.send(
                Envelope(type="tts.speak", session_id="s", seq=0, ts_ms=0),
                TtsSpeak(
                    utterance_id="u_%s" % event.envelope.turn_id,
                    text=response,
                    flush=False,
                ),
            )
            await gw.send(
                Envelope(type="tts.stream_end", session_id="s", seq=0, ts_ms=0),
                TtsStreamEnd(),
            )
    return events


def _by_turn(events, turn_id, kind):
    return [e.payload for e in events if e.envelope.turn_id == turn_id and isinstance(e.payload, kind)]


async def test_caller_turn_yields_word_accumulating_partials_then_final():
    events = await _drive(LocalGatewaySimulator(booking_happy_path(), ManualClock()))

    finals = [e.payload for e in events if isinstance(e.payload, SttFinal)]
    assert len(finals) == 2  # booking_happy_path has two caller turns

    partials = _by_turn(events, "turn_0", SttPartial)
    assert [p.text for p in partials] == [
        "I",
        "I want",
        "I want to",
        "I want to book",
        "I want to book a",
        "I want to book a demo.",
    ]
    stabilities = [p.stability for p in partials]
    assert stabilities == sorted(stabilities)  # monotonically rising
    assert stabilities[-1] == 1.0
    assert _by_turn(events, "turn_0", SttFinal)[0].text == "I want to book a demo."


async def test_tts_speak_produces_started_mark_finished_playback():
    events = await _drive(LocalGatewaySimulator(booking_happy_path(), ManualClock()))
    states = [e.payload.state for e in events if isinstance(e.payload, TtsPlayback)]
    assert states.count("started") == 2
    assert states.count("mark") >= 2
    assert states.count("finished") == 2
    # a finished mark reports the full utterance length
    finished = [p for p in (e.payload for e in events) if isinstance(p, TtsPlayback) and p.state == "finished"]
    assert finished[0].mark_chars == len("Happy to help")


async def test_session_lifecycle_brackets_the_call():
    events = await _drive(LocalGatewaySimulator(booking_happy_path(), ManualClock()))
    assert isinstance(events[0].payload, SessionStarted)
    assert isinstance(events[-1].payload, SessionEnded)


async def test_empty_caller_utterance_yields_a_final_with_no_partials():
    from lucy.evals import SyntheticCallScenario, SyntheticTurn

    scenario = SyntheticCallScenario(
        name="silence",
        objective="recover from silence",
        turns=[SyntheticTurn(speaker="caller", text="")],
        expected_outcome="silence",
    )
    events = await _drive(LocalGatewaySimulator(scenario, ManualClock()))
    assert _by_turn(events, "turn_0", SttPartial) == []  # no words -> no partials
    finals = _by_turn(events, "turn_0", SttFinal)
    assert len(finals) == 1 and finals[0].text == ""


async def test_barge_in_turn_emits_barge_in_and_no_finished():
    gw = LocalGatewaySimulator(
        booking_happy_path(), ManualClock(), barge_in_turns={0}
    )
    events = await _drive(gw)
    t0 = [e.payload for e in events if e.envelope.turn_id == "turn_0"]
    assert any(getattr(p, "during", None) == "speaking" for p in t0)  # a BargeIn fired
    t0_playback_states = [p.state for p in t0 if isinstance(p, TtsPlayback)]
    assert "started" in t0_playback_states
    assert "finished" not in t0_playback_states  # the utterance was cut off


async def test_multi_clause_turn_plays_every_clause_with_one_terminal_finished():
    # card 64: N clauses -> started once (first clause), a mark per clause in
    # order, and exactly ONE terminal finished carrying the last clause.
    from lucy.evals import SyntheticCallScenario, SyntheticTurn

    scenario = SyntheticCallScenario(
        name="one_turn",
        objective="multi-clause playback",
        turns=[SyntheticTurn(speaker="caller", text="hi")],
        expected_outcome="booked",
    )
    gw = LocalGatewaySimulator(scenario, ManualClock())
    clauses = ["First clause.", "Second one?", "Third."]

    events = []
    async for event in gw.events():
        events.append(event)
        if isinstance(event.payload, SttFinal):
            for i, text in enumerate(clauses):
                await gw.send(
                    Envelope(type="tts.speak", session_id="s", seq=0, ts_ms=0),
                    TtsSpeak(utterance_id="u%d" % i, text=text, flush=True),
                )
            await gw.send(
                Envelope(type="tts.stream_end", session_id="s", seq=0, ts_ms=0),
                TtsStreamEnd(),
            )

    playback = [e.payload for e in events if isinstance(e.payload, TtsPlayback)]
    assert [p.state for p in playback] == ["started", "mark", "mark", "mark", "finished"]
    assert playback[0].utterance_id == "u0"
    marks = [p for p in playback if p.state == "mark"]
    assert [(m.utterance_id, m.mark_chars) for m in marks] == [
        ("u0", len(clauses[0])),
        ("u1", len(clauses[1])),
        ("u2", len(clauses[2])),
    ]
    terminal = playback[-1]
    assert terminal.utterance_id == "u2" and terminal.mark_chars == len(clauses[2])
