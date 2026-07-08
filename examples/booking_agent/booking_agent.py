"""Reference: a booking agent built only on public Lucy APIs - no vertical code.

This is the open, sanitized counterpart to product verticals (e.g. Pili, which
lives in its own repo). It runs offline on the local simulators: a voice turn
plus an MCP-driven CRM upsert and calendar hold, all through documented imports.

Run it:  python examples/booking_agent/booking_agent.py
"""

import asyncio

from lucy import AgentSpec, AudioChunk, LucySpec, McpClient, VoiceAgent, VoiceSpec
from lucy.testing import LocalMcpCommandTransport

ALLOWED_TOOLS = ["crm.upsert_lead", "calendar.hold_slot"]


async def main() -> None:
    spec = LucySpec(
        agent=AgentSpec(
            name="Booking Agent", goal="Book a meeting.", prompt="Qualify and book."
        ),
        voice=VoiceSpec(transport="sim", stt_provider="local", tts_provider="local"),
    )
    agent = VoiceAgent(spec)
    mcp = McpClient(LocalMcpCommandTransport(), allowed_tools=ALLOWED_TOOLS)

    with agent.start_session("booking-session") as session:
        await session.user_audio(
            AudioChunk(session_id="booking-session", data=b"book me a demo", sequence=0)
        )
        await session.synthesize(session.last_response or "Sure, let's book it.")
        lead = await mcp.call_tool(
            "crm",
            "upsert_lead",
            {"lead_id": "lead_demo", "booking_status": "held"},
            session_id="booking-session",
            turn_id="booking-session-t0",
        )
        slot = await mcp.call_tool(
            "calendar",
            "hold_slot",
            {"lead_id": "lead_demo", "requested_slot": "2026-06-09T10:00:00+02:00"},
            session_id="booking-session",
            turn_id="booking-session-t0",
        )
        print("crm upsert:", lead)
        print("calendar hold:", slot)


if __name__ == "__main__":
    asyncio.run(main())
