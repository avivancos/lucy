from fastapi.testclient import TestClient

from lucy.api.app import create_app


def test_pili_api_routes_are_present_in_openapi():
    schema = create_app().openapi()

    for route in [
        "/pili/health",
        "/pili/voice/events",
        "/pili/bookings",
    ]:
        assert route in schema["paths"]


def test_pili_health_contract():
    response = TestClient(create_app()).get("/pili/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "pili-api",
        "status": "ok",
        "lucy_compatible": True,
    }


def test_pili_voice_event_contract():
    response = TestClient(create_app()).post(
        "/pili/voice/events",
        json={
            "session_id": "sess_demo",
            "lead_id": "lead_demo",
            "funnel_stage": "booked",
            "sentiment": "positive",
            "cost_per_minute": 0.031667,
            "transcript_excerpt": "Tuesday morning would be perfect.",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "event_id": "pili_evt_sess_demo_booked",
        "accepted": True,
        "crm_sync_status": "queued",
        "mcp_commands": [
            {
                "command_id": "mcp_crm_upsert_lead_1",
                "server": "crm",
                "tool": "upsert_lead",
                "status": "queued",
            }
        ],
        "mcp_audit_count": 1,
    }


def test_pili_booking_hold_contract():
    response = TestClient(create_app()).post(
        "/pili/bookings",
        json={
            "session_id": "sess_demo",
            "lead_id": "lead_demo",
            "requested_slot": "2026-06-09T10:00:00+02:00",
            "timezone": "Europe/Madrid",
            "source": "voice",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "booking_id": "pili_booking_lead_demo_20260609t1000000200",
        "status": "held",
        "crm_sync_status": "queued",
        "mcp_commands": [
            {
                "command_id": "mcp_crm_upsert_lead_1",
                "server": "crm",
                "tool": "upsert_lead",
                "status": "queued",
            },
            {
                "command_id": "mcp_calendar_hold_slot_2",
                "server": "calendar",
                "tool": "hold_slot",
                "status": "queued",
            },
        ],
        "mcp_audit_count": 2,
    }


def test_pili_booking_requires_mcp_calendar_permission():
    client = TestClient(create_app(allowed_pili_tools=["crm.upsert_lead"]))

    response = client.post(
        "/pili/bookings",
        json={
            "session_id": "sess_demo",
            "lead_id": "lead_demo",
            "requested_slot": "2026-06-09T10:00:00+02:00",
            "timezone": "Europe/Madrid",
            "source": "voice",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "MCP tool is not allowed: calendar.hold_slot"
    }


def test_mcp_servers_include_pili_crm_and_calendar_tools():
    response = TestClient(create_app()).get("/mcp/servers")

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "crm",
            "status": "configured",
            "allowed_tools": ["crm.upsert_lead"],
        },
        {
            "name": "calendar",
            "status": "configured",
            "allowed_tools": ["calendar.hold_slot"],
        },
    ]
