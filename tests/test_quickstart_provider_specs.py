import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_quickstart_falls_back_to_simulators_without_keys():
    environment = dict(os.environ)
    for name in (
        "DEEPGRAM_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "OPENAI_API_KEY",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "LUCY_QUICKSTART_STT_SPEC": "deepgram/nova-3",
            "LUCY_QUICKSTART_TTS_SPEC": "elevenlabs/flash-v2.5",
        }
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "quickstart_voice_agent.py")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "hello there" in result.stdout
    assert "DEEPGRAM_API_KEY" in result.stderr
    assert "ELEVENLABS_API_KEY" in result.stderr
