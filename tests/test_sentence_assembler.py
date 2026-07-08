from lucy.speech import MIN_FLUSH_CHARS, SentenceAssembler, TtsPlanner
from lucy.transport.schema import TtsCancel, TtsPlayback, TtsSpeak


# -- C4: SentenceAssembler ---------------------------------------------------


def test_feed_flushes_clause_at_boundary_past_min_length():
    assembler = SentenceAssembler(min_flush_chars=10)
    assert assembler.feed("Hello there,") == [
        "Hello there,"
    ]  # 12 chars, comma boundary


def test_short_clause_stays_buffered_until_next_boundary():
    assembler = SentenceAssembler(min_flush_chars=10)
    assert assembler.feed("Hi,") == []  # 3 chars < 10 -> not flushed
    assert assembler.feed(" ready to book?") == ["Hi, ready to book?"]


def test_no_boundary_never_flushes_until_finalize():
    assembler = SentenceAssembler(min_flush_chars=10)
    assert assembler.feed("this has no terminator yet") == []


def test_finalize_flushes_trailing_text_and_resets():
    assembler = SentenceAssembler(min_flush_chars=10)
    assembler.feed("A trailing remainder")
    assert assembler.finalize() == ["A trailing remainder"]
    assert assembler.finalize() == []  # reset after finalize


def test_default_min_flush_chars_is_the_named_constant():
    assert SentenceAssembler().feed("Yes.") == []  # 4 chars < MIN_FLUSH_CHARS
    assert MIN_FLUSH_CHARS > 0


# -- C5: TtsPlanner ----------------------------------------------------------


def test_planner_assigns_ordered_unique_utterance_ids():
    planner = TtsPlanner()
    a = planner.plan("First clause.")
    b = planner.plan("Second clause.")
    assert isinstance(a, TtsSpeak) and a.flush is True
    assert a.utterance_id != b.utterance_id
    assert [a.utterance_id, b.utterance_id] == sorted([a.utterance_id, b.utterance_id])


def test_spoken_text_truncates_to_last_mark_chars_on_barge_in():
    planner = TtsPlanner()
    first = planner.plan("Hello there.")
    second = planner.plan("How can I help you today?")

    planner.record_playback(
        TtsPlayback(utterance_id=first.utterance_id, state="started", mark_chars=0)
    )
    planner.record_playback(
        TtsPlayback(
            utterance_id=first.utterance_id,
            state="finished",
            mark_chars=len("Hello there."),
        )
    )
    planner.record_playback(
        TtsPlayback(utterance_id=second.utterance_id, state="started", mark_chars=0)
    )
    planner.record_playback(
        TtsPlayback(utterance_id=second.utterance_id, state="mark", mark_chars=7)
    )

    assert planner.spoken_text() == "Hello there. How can"


def test_spoken_text_is_all_finished_utterances_when_uninterrupted():
    planner = TtsPlanner()
    a = planner.plan("Sure.")
    b = planner.plan("Tuesday works.")
    for utt in (a, b):
        planner.record_playback(
            TtsPlayback(
                utterance_id=utt.utterance_id,
                state="finished",
                mark_chars=len(utt.text),
            )
        )
    assert planner.spoken_text() == "Sure. Tuesday works."


def test_cancel_directive_cancels_all():
    directive = TtsPlanner().cancel_directive()
    assert isinstance(directive, TtsCancel) and directive.utterance_id == "all"
