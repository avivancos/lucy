import json
import wave
from pathlib import Path

from lucy.evals import booking_happy_path

FIXTURES = Path(__file__).parent / "fixtures" / "audio"
WAV = FIXTURES / "booking_caller_8k.wav"
TIMELINE = FIXTURES / "booking_caller.timeline.json"


def test_wav_fixture_is_8k_mono_pcm16():
    with wave.open(str(WAV), "rb") as audio:
        assert audio.getframerate() == 8_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2


def test_timeline_matches_scenario_caller_lines_and_wav_duration():
    timeline = json.loads(TIMELINE.read_text(encoding="utf-8"))
    with wave.open(str(WAV), "rb") as audio:
        duration_ms = audio.getnframes() / audio.getframerate() * 1_000
    caller_lines = [
        turn.text for turn in booking_happy_path().turns if turn.speaker == "caller"
    ]

    assert timeline["wav"] == WAV.name
    assert [item["text"] for item in timeline["utterances"]] == caller_lines
    previous = -1
    for utterance in timeline["utterances"]:
        assert 0 <= utterance["start_ms"] < utterance["end_ms"] <= duration_ms
        for word in utterance["words"]:
            assert previous < word["at_ms"] < utterance["end_ms"]
            previous = word["at_ms"]
