"""ElevenLabs models supported by the Lucy TTS plugin."""

from lucy.providers import Capability, ModelInfo

MODELS = [
    ModelInfo(
        provider="elevenlabs",
        model="flash-v2.5",
        capabilities=[Capability.TTS],
        recommended_for=["lowest-latency conversational TTS"],
        low_latency=True,
    ),
    ModelInfo(
        provider="elevenlabs",
        model="turbo-v2.5",
        capabilities=[Capability.TTS],
        recommended_for=["balanced conversational TTS"],
        low_latency=True,
    ),
]

MODEL_IDS = {
    "flash-v2.5": "eleven_flash_v2_5",
    "turbo-v2.5": "eleven_turbo_v2_5",
}
