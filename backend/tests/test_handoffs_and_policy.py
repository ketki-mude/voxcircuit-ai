"""Handoff rules and the policy monitor during live calls."""

import uuid
from typing import Any

from sqlalchemy import Engine, select

from app.live.events import InputTranscript, TurnComplete
from app.live.fake import scripted_reply
from app.models import AuditLog, Call, Handoff, Message, PolicyEvent
from tests.conftest import (
    START_AUDIO,
    AppFactory,
    FakeSessions,
    auth,
    of_type,
    open_call,
    receive_until,
)
from tests.helpers import bookable_day, booking_script, tool_call


def handoffs(db_engine: Engine, call_id: uuid.UUID) -> list[tuple[str, str]]:
    with db_engine.connect() as conn:
        rows = conn.execute(
            select(Handoff.reason, Handoff.status).where(Handoff.call_id == call_id)
        ).all()
    return [(row.reason, row.status) for row in rows]


def flagged(db_engine: Engine, call_id: uuid.UUID) -> bool:
    with db_engine.connect() as conn:
        return bool(conn.scalar(select(Call.flagged).where(Call.id == call_id)))


def say(ws: Any, text: str) -> None:
    ws.send_json({"type": "user_text", "text": text})


def test_a_claim_hands_off_once(make_client: AppFactory, db_engine: Engine) -> None:
    sessions = FakeSessions([scripted_reply("Hi.")])
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "I need to file a claim for my accident.")
        handoff = receive_until(ws, "handoff")[-1]
        say(ws, "This is ridiculous.")  # Upset now too, but one open handoff is enough.
        ws.send_json({"type": "end"})
        rest = receive_until(ws, "call_ended")

    assert handoff == {
        "type": "handoff",
        "reason": "claim_or_complaint",
        "label": "Claim or complaint",
    }
    assert not of_type(rest, "handoff")
    assert handoffs(db_engine, call_id) == [("claim_or_complaint", "open")]
    assert flagged(db_engine, call_id)
    notices = [t for t in sessions.created[0].sent_text if "licensed human agent" in t]
    assert notices == [
        "[Northstar system] This call needs a licensed human agent (claim or complaint). "
        "Tell the caller briefly that a licensed agent will follow up with them. "
        "Do not share any account history."
    ]


def test_out_of_scope_requests_hand_off_when_they_persist(
    make_client: AppFactory, db_engine: Engine
) -> None:
    client = make_client(sessions=FakeSessions([scripted_reply("Hi.")]))

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "How much would it cost?")
        receive_until(ws, "turn_complete")
        assert handoffs(db_engine, call_id) == []
        say(ws, "Come on, just give me a quote.")
        assert receive_until(ws, "handoff")[-1]["reason"] == "out_of_scope"


def test_correcting_the_same_detail_twice_hands_off(
    make_client: AppFactory, db_engine: Engine
) -> None:
    client = make_client(
        sessions=FakeSessions(
            [
                scripted_reply("Hi."),
                [tool_call("create_or_update_lead", zip_code="02139")],
                [tool_call("create_or_update_lead", zip_code="02140")],  # First correction.
                [tool_call("create_or_update_lead", zip_code="02141")],  # Second correction.
                scripted_reply("Let me get you an agent."),
            ]
        )
    )

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "My ZIP code is 02139.")
        frames = receive_until(ws, "handoff")

    assert len(of_type(frames, "tool")) == 3
    assert frames[-1]["reason"] == "repeated_correction"
    assert handoffs(db_engine, call_id) == [("repeated_correction", "open")]


def test_maya_can_ask_for_a_human(make_client: AppFactory, db_engine: Engine) -> None:
    sessions = FakeSessions(
        [
            scripted_reply("Hi."),
            [tool_call("handoff_to_human", reason="caller_upset")],
            scripted_reply("A licensed agent will follow up with you."),
        ]
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "Ugh, never mind.")
        frames = receive_until(ws, "turn_complete")

    assert of_type(frames, "handoff")[0]["reason"] == "caller_upset"
    assert handoffs(db_engine, call_id) == [("caller_upset", "open")]
    # The tool response already tells Maya what to say; no second notice.
    assert not any("licensed human agent" in t for t in sessions.created[0].sent_text)


def test_spoken_requests_hand_off_too(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client(
        sessions=FakeSessions(
            [
                scripted_reply("Hi."),
                [
                    InputTranscript(text="I want to speak to a real person.", finished=True),
                    TurnComplete(),
                ],
            ],
            audio_turn_bytes=3_200,
        )
    )

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws, START_AUDIO)
        ws.send_bytes(bytes(3_200))
        assert receive_until(ws, "handoff")[-1]["reason"] == "asked_for_human"

    assert handoffs(db_engine, call_id) == [("asked_for_human", "open")]


def test_an_incomplete_booking_hands_off(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client(sessions=FakeSessions(booking_script(bookable_day(3))))
    client.app.state.crm.faults.mode = "fail_always"  # type: ignore[attr-defined]

    with client.websocket_connect("/ws/call") as ws:
        call_id, token = open_call(ws)
        say(ws, "I'd like to switch insurers.")
        approval_id = receive_until(ws, "approval_requested")[-1]["approval_id"]
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=auth(token)
        )
        frames = receive_until(ws, "handoff")

    assert of_type(frames, "booking_result")[0]["success"] is False
    assert frames[-1]["reason"] == "booking_incomplete"
    assert handoffs(db_engine, call_id) == [("booking_incomplete", "open")]


def test_a_policy_violation_is_logged_flagged_and_corrected(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions(
        [
            scripted_reply("Hi."),
            scripted_reply("It's about $120 a month."),
            scripted_reply("Sorry, I misspoke. A licensed agent can discuss pricing."),
        ]
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "Roughly what would I pay?")
        receive_until(ws, "turn_complete")  # The violation.
        frames = receive_until(ws, "turn_complete")  # The correction.

    assert of_type(frames, "message")[-1]["text"].startswith("Sorry, I misspoke.")
    assert (
        sessions.created[0]
        .sent_text[-1]
        .startswith(
            "[Northstar system] Compliance correction for your last reply. You stated a price"
        )
    )
    with db_engine.connect() as conn:
        event = conn.execute(select(PolicyEvent).where(PolicyEvent.call_id == call_id)).one()
        message_text = conn.scalar(select(Message.text).where(Message.id == event.message_id))
        audit = list(conn.scalars(select(AuditLog.event).where(AuditLog.call_id == call_id)))
    assert (event.rule, event.excerpt) == ("price", "It's about $120 a month.")
    assert message_text == "It's about $120 a month."
    assert "policy.violation" in audit
    assert flagged(db_engine, call_id)


def test_claiming_a_booking_that_did_not_happen_is_corrected(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions(
        [scripted_reply("Hi."), scripted_reply("Great, you're all booked for Tuesday.")]
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        call_id, _ = open_call(ws)
        say(ws, "Tuesday works.")
        receive_until(ws, "turn_complete")
        receive_until(ws, "turn_complete")

    with db_engine.connect() as conn:
        rules = list(conn.scalars(select(PolicyEvent.rule).where(PolicyEvent.call_id == call_id)))
    assert rules == ["unconfirmed_booking"]
    assert "Tell the caller it is not booked yet." in sessions.created[0].sent_text[-1]


def test_announcing_a_real_booking_is_not_flagged(
    make_client: AppFactory, db_engine: Engine
) -> None:
    script = booking_script(bookable_day(3))
    script[-1] = scripted_reply("You're all booked. A licensed agent will call you then.")
    client = make_client(sessions=FakeSessions(script))

    with client.websocket_connect("/ws/call") as ws:
        call_id, token = open_call(ws)
        say(ws, "I'd like to switch insurers.")
        approval_id = receive_until(ws, "approval_requested")[-1]["approval_id"]
        receive_until(ws, "turn_complete")
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=auth(token)
        )
        frames = receive_until(ws, "turn_complete")

    assert of_type(frames, "message")[-1]["text"].startswith("You're all booked.")
    with db_engine.connect() as conn:
        assert conn.scalar(select(PolicyEvent.id).where(PolicyEvent.call_id == call_id)) is None
