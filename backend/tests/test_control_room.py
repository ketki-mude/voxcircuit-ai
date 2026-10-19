"""Control room endpoints: metrics, calls, call detail, and the handoff queue."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app.live.fake import scripted_reply
from app.models import AuditLog, Call, Handoff
from tests.conftest import (
    AppFactory,
    FakeSessions,
    auth,
    of_type,
    open_call,
    receive_until,
    team_headers,
)
from tests.helpers import bookable_day, booking_script


def summary(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.get("/api/control-room/summary", headers=headers)
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def test_summary_counts_live_calls_from_the_database(
    make_client: AppFactory, db_engine: Engine
) -> None:
    script = [
        scripted_reply("Hi."),
        scripted_reply("It would be about $120 a month."),  # A policy flag.
        scripted_reply("Sorry, I misspoke."),
    ]
    client = make_client(sessions=FakeSessions(script))
    headers = team_headers(client, db_engine, "viewer")
    before = summary(client, headers)

    with client.websocket_connect("/ws/call") as ws:
        open_call(ws)
        ws.send_json({"type": "user_text", "text": "Roughly what would I pay?"})
        receive_until(ws, "turn_complete")
        receive_until(ws, "turn_complete")
        ws.send_json({"type": "user_text", "text": "Can I talk to a real person?"})
        receive_until(ws, "handoff")
        ws.send_json({"type": "end"})
        receive_until(ws, "call_ended")

    after = summary(client, headers)
    delta = {k: after["metrics"][k] - before["metrics"][k] for k in after["metrics"]}
    assert delta == {"calls_today": 1, "booked_today": 0, "needs_human": 1, "policy_flags_today": 1}
    assert after["usage"]["calls_today"] == before["usage"]["calls_today"] + 1
    assert after["usage"]["daily_call_cap"] == 10_000
    assert after["usage"]["latency_p50_ms"] is not None
    assert after["usage"]["latency_samples"] > before["usage"]["latency_samples"]
    assert after["settings"]["model"] == "gemini-3.8-live"


def test_calls_list_and_detail(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client(sessions=FakeSessions(booking_script(bookable_day(3))))
    viewer = team_headers(client, db_engine, "viewer")
    agent = team_headers(client, db_engine, "agent")

    with client.websocket_connect("/ws/call") as ws:
        call_id, token = open_call(ws)
        ws.send_json({"type": "user_text", "text": "I'd like to switch insurers."})
        approval_id = receive_until(ws, "approval_requested")[-1]["approval_id"]
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=auth(token)
        )
        receive_until(ws, "booking_result")
        ws.send_json({"type": "end"})
        receive_until(ws, "call_ended")

    rows = client.get("/api/calls", headers=viewer).json()
    row = next(r for r in rows if r["id"] == str(call_id))
    assert (row["booked"], row["user_turns"], row["status"], row["purpose"]) == (
        True,
        1,
        "ended",
        "live",
    )
    assert row["duration_seconds"] is not None
    assert all(
        r["purpose"] == "evaluation"
        for r in client.get("/api/calls?purpose=evaluation", headers=viewer).json()
    )

    assert client.get(f"/api/calls/{call_id}", headers=viewer).status_code == 403  # Transcripts.
    detail = client.get(f"/api/calls/{call_id}", headers=agent).json()
    assert detail["call"]["id"] == str(call_id)
    assert [m["role"] for m in detail["messages"]][:2] == ["assistant", "user"]
    assert [t["name"] for t in detail["tool_calls"]] == [
        "create_or_update_lead",
        "check_availability",
        "request_booking",
    ]
    approval = detail["approvals"][0]
    assert approval["status"] == "approved" and approval["outcome"]["success"] is True
    assert [(s["step"], s["status"]) for s in approval["steps"]] == [
        ("crm_upsert", "done"),
        ("calendar_event", "done"),
    ]
    assert "booking.completed" in [a["event"] for a in detail["audit"]]


def test_taking_over_a_live_call_hands_it_to_the_agent(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions(
        [
            scripted_reply("Hi."),
            scripted_reply("Of course."),
            scripted_reply("A licensed agent will follow up."),
            scripted_reply("Ada will contact you shortly. Thanks for calling. Goodbye."),
        ]
    )
    client = make_client(sessions=sessions)
    viewer = team_headers(client, db_engine, "viewer")
    agent = team_headers(client, db_engine, "agent")

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        ws.send_json({"type": "user_text", "text": "Can I talk to a real person?"})
        receive_until(ws, "handoff")
        receive_until(ws, "turn_complete")
        receive_until(ws, "turn_complete")

        queue = client.get("/api/handoffs", headers=viewer).json()
        item = next(h for h in queue if h["call_id"] == str(call_id))
        assert (item["label"], item["call_active"]) == ("Caller asked for a person", True)
        assert (
            client.post(f"/api/handoffs/{item['id']}/take-over", headers=viewer).status_code == 403
        )
        taken = client.post(f"/api/handoffs/{item['id']}/take-over", headers=agent)
        assert taken.status_code == 200
        assert (taken.json()["status"], taken.json()["taken_by"]) == ("taken", "Agent Tester")
        again = client.post(f"/api/handoffs/{item['id']}/take-over", headers=agent)
        assert again.status_code == 409

        frames = receive_until(ws, "call_ended")

    assert of_type(frames, "handoff_taken") == [{"type": "handoff_taken", "agent_name": "Agent"}]
    assert of_type(frames, "message")[-1]["text"].endswith("Goodbye.")
    assert frames[-1]["reason"] == "handed_off"
    assert "A licensed agent, Agent, has taken over this call." in sessions.created[0].sent_text[-1]
    assert item["id"] not in [h["id"] for h in client.get("/api/handoffs", headers=viewer).json()]
    with db_engine.connect() as conn:
        assert conn.scalar(select(Handoff.status).where(Handoff.call_id == call_id)) == "taken"
        assert conn.scalar(select(Call.end_reason).where(Call.id == call_id)) == "handed_off"
        events = list(conn.scalars(select(AuditLog.event).where(AuditLog.call_id == call_id)))
    assert "handoff.taken" in events


def test_unknown_handoff_is_404(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client()
    agent = team_headers(client, db_engine, "agent")
    response = client.post(
        "/api/handoffs/00000000-0000-0000-0000-000000000000/take-over", headers=agent
    )
    assert response.status_code == 404
