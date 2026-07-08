"""Quickstart: a working voice agent in under 30 lines, no API keys, no network."""

import asyncio

from lucy import AgentSpec, AudioChunk, LucySpec, VoiceAgent, VoiceSpec


async def main() -> None:
    spec = LucySpec(
        agent=AgentSpec(
            name="Quickstart Agent", goal="Help the caller.", prompt="Be helpful."
        ),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    agent = VoiceAgent(spec)  # console trace exporter by default
    with agent.start_session("quickstart-session") as session:
        for event in await session.user_audio(
            AudioChunk(session_id="quickstart-session", data=b"hello there", sequence=0)
        ):
            print("inbound:", event)
        print("agent response:", session.last_response)
        for event in await session.synthesize(session.last_response or "Hello!"):
            print("outbound:", event)


if __name__ == "__main__":
    asyncio.run(main())
