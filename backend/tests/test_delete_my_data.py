"""DELETE /api/customers/me: a verified caller removes everything stored about them."""

import uuid

from sqlalchemy import Engine, func, select

from app.customers import REDACTED
from app.live.fake import ScriptStep, scripted_reply
from app.models import (
    AuditLog,
    Call,
    Customer,
    Lead,
    Message,
    PolicyEvent,
    VerificationAttempt,
)
from tests.conftest import AppFactory, SessionsInOrder, auth, of_type, open_call, receive_until
from tests.helpers import LEAD, bookable_day, booking_script, tool_call


def count(db_engine: Engine, model: type, *where: object) -> int:
    with db_engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(model).where(*where)) or 0  # type: ignore[arg-type]


def test_a_verified_caller_can_delete_their_data(
    make_client: AppFactory, db_engine: Engine
) -> None:
    email = f"delete-{uuid.uuid4().hex[:8]}@example.com"
    returning_call: list[list[ScriptStep]] = [
        scripted_reply("Hi, I'm Maya."),
        scripted_reply("Welcome back. That's $90 a month."),  # A policy violation.
        scripted_reply("Sorry, I can't quote prices."),  # After the correction.
        [tool_call("get_previous_interactions")],  # After the "data deleted" notice.
        scripted_reply("Your data has been deleted."),
    ]
    sessions = SessionsInOrder(
        booking_script(bookable_day(3), LEAD | {"email": email}), returning_call
    )
    client = make_client(sessions=sessions)
    calendar, crm = client.app.state.calendar, client.app.state.crm  # type: ignore[attr-defined]

    # First call: book, which creates the customer, the CRM contact and the calendar event.
    with client.websocket_connect("/ws/call") as ws:
        first_call, token = open_call(ws)
        ws.send_json({"type": "user_text", "text": "I'd like to switch insurers."})
        approval_id = receive_until(ws, "approval_requested")[-1]["approval_id"]
        client.post(
            f"/api/approvals/{approval_id}", json={"decision": "approve"}, headers=auth(token)
        )
        receive_until(ws, "booking_result")
    assert count(db_engine, Customer, Customer.email == email) == 1
    assert len(calendar.events) == 1 and email in crm.contacts

    # Second call: verify, then delete.
    with client.websocket_connect("/ws/call") as ws:
        call_id, token = open_call(ws)
        client.post("/api/verify/start", json={"email": email}, headers=auth(token))
        code = receive_until(ws, "verification_started")[-1]["demo_code"]
        client.post("/api/verify/check", json={"code": code}, headers=auth(token))
        receive_until(ws, "turn_complete")
        receive_until(ws, "turn_complete")

        response = client.delete("/api/customers/me", headers=auth(token))
        frames = receive_until(ws, "turn_complete")
        after = client.delete("/api/customers/me", headers=auth(token))

    assert response.status_code == 200
    assert response.json() == {
        "deleted": True,
        "calls_deleted": 1,
        "crm_contact_deleted": True,
        "calendar_events_deleted": 1,
    }
    assert of_type(frames, "data_deleted") == [{"type": "data_deleted"}]
    assert after.status_code == 403  # No longer verified.

    # Gone: the customer, the earlier call with its transcript and lead, and what this
    # call stored before the deletion. Kept: this call, and the audit log.
    assert count(db_engine, Customer, Customer.email == email) == 0
    assert count(db_engine, Call, Call.id == first_call) == 0
    assert count(db_engine, Message, Message.call_id == first_call) == 0
    assert count(db_engine, Lead, Lead.email == email) == 0
    assert count(db_engine, VerificationAttempt, VerificationAttempt.call_id == call_id) == 0
    with db_engine.connect() as conn:
        texts = list(conn.scalars(select(Message.text).where(Message.call_id == call_id)))
        verified = conn.scalar(select(Call.verified_customer_id).where(Call.id == call_id))
    assert texts == ["Your data has been deleted."]
    assert verified is None
    with db_engine.connect() as conn:
        policy = conn.execute(select(PolicyEvent).where(PolicyEvent.call_id == call_id)).one()
    assert (policy.rule, policy.excerpt, policy.message_id) == ("price", REDACTED, None)
    assert calendar.events == {} and email not in crm.contacts
    assert count(db_engine, AuditLog, AuditLog.call_id == first_call) > 0
    assert (
        count(
            db_engine, AuditLog, AuditLog.event == "customer.deleted", AuditLog.call_id == call_id
        )
        == 1
    )

    fake = sessions.created[1]
    assert "stored data was deleted" in fake.sent_text[-1]
    assert fake.last_response("get_previous_interactions")["error"].startswith(
        "The caller is not verified."
    )


def test_deleting_needs_a_verified_customer(make_client: AppFactory) -> None:
    client = make_client(sessions=SessionsInOrder([scripted_reply("Hi.")]))

    with client.websocket_connect("/ws/call") as ws:
        _, token = open_call(ws)
        client.post("/api/verify/start", json={"email": "nobody@example.com"}, headers=auth(token))
        code = receive_until(ws, "verification_started")[-1]["demo_code"]
        client.post("/api/verify/check", json={"code": code}, headers=auth(token))
        # Verified the email, but it belongs to no customer: nothing to delete, no access.
        assert client.delete("/api/customers/me", headers=auth(token)).status_code == 403
