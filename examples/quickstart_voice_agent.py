import asyncio
import os
import wave

from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec


async def main() -> None:
    stt = os.getenv("LUCY_QUICKSTART_STT_SPEC", "local")
    tts = os.getenv("LUCY_QUICKSTART_TTS_SPEC", "local")
    audio_path = os.getenv("LUCY_QUICKSTART_AUDIO_FILE")
    audio = wave.open(audio_path).readframes(1 << 24) if audio_path else b"hello there"
    spec = LucySpec(
        agent=AgentSpec(name="Quickstart Agent", goal="Help.", prompt="Be helpful."),
        voice=VoiceSpec(transport="sim", stt_provider=stt, tts_provider=tts),
    )
    agent = VoiceAgent(spec)  # console trace exporter by default
    deadline = os.getenv("LUCY_QUICKSTART_STT_DEADLINE_MS")
    agent.stt_deadline_ms = int(deadline) if deadline else agent.stt_deadline_ms
    with agent.start_session("quickstart-session") as session:
        for event in await session.user_audio(
            AudioChunk(session_id="quickstart-session", data=audio, sequence=0)
        ):
            print("inbound:", event)
        await session.synthesize(session.last_response or "Hello!")


if __name__ == "__main__":
    asyncio.run(main())
