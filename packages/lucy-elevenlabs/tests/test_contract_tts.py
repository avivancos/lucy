from pathlib import Path

import pytest
from pydantic import SecretStr

from lucy.testing.contracts import TtsContractSuite
from lucy.testing.replay import ReplayTransport, StalledTransport, load_fixture
from lucy_elevenlabs.settings import ElevenLabsSettings
from lucy_elevenlabs.tts import ElevenLabsTtsAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "tts_short_sentence.jsonl"
TEXT = "Lucy providers can speak naturally."


def _settings() -> ElevenLabsSettings:
    return ElevenLabsSettings(
        api_key=SecretStr("fixture-key"),
        voice_id="fixture-voice",
    )


class TestElevenLabsTtsContract(TtsContractSuite):
    def make_provider(self):
        return ElevenLabsTtsAdapter(
            model="flash-v2.5",
            settings=_settings(),
            transport=ReplayTransport(load_fixture(FIXTURE)),
        )

    def text(self):
        return TEXT

    def make_stalled_provider(self):
        return ElevenLabsTtsAdapter(
            model="flash-v2.5",
            settings=_settings(),
            transport=StalledTransport(),
        )

    def make_malformed_case(self):
        return (
            ElevenLabsTtsAdapter(
                model="flash-v2.5",
                settings=_settings(),
                transport=StalledTransport(),
            ),
            "   ",
        )


@pytest.mark.live
class TestElevenLabsTtsLiveContract(TestElevenLabsTtsContract):
    def make_provider(self):
        return ElevenLabsTtsAdapter(
            model="flash-v2.5",
            settings=ElevenLabsSettings(),
        )
