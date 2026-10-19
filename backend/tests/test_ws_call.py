import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, not_, select
from starlette.websockets import WebSocketDisconnect

from app.calls.recorder import OFFLINE_EVALUATION, start_of_today
from app.live.base import LiveQuotaError
from app.live.events import AudioChunk, Interrupted, OutputTranscript, TurnComplete, Usage
from app.live.fake import scripted_reply
from app.models import AuditLog, Call, Message, UsageLog
from app.prompts import KICKOFF_TEXT
from tests.conftest import START, AppFactory, FakeSessions, of_type, receive_until

GREETING = "Hi, I'm Maya, an AI assistant for Northstar Insurance."
ONE_SECOND_OF_AUDIO = AudioChunk(data=bytes(48_000))


def count_calls(db_engine: Engine) -> int:
    with db_engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(Call)) or 0


def test_text_call_stores_messages_usage_and_audit(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions(
        [
            scripted_reply(GREETING),
            [
                ONE_SECOND_OF_AUDIO,
                OutputTranscript(text="Thanks, "),
                OutputTranscript(text="Sam."),
                Usage(prompt_tokens=10, response_tokens=20, total_tokens=30),
                TurnComplete(),
            ],
        ]
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        started = receive_until(ws, "call_started")[-1]
        call_id = uuid.UUID(started["call_id"])
        assert started["max_turns"] == 30

        greeting = receive_until(ws, "turn_complete")
        assert "".join(m["text"] for m in of_type(greeting, "assistant_delta")) == GREETING
        saved_greeting = of_type(greeting, "message")[0]
        assert (saved_greeting["seq"], saved_greeting["role"], saved_greeting["text"]) == (
            1,
            "assistant",
            GREETING,
        )
        assert saved_greeting["interrupted"] is False

        ws.send_json({"type": "user_text", "text": "  My name is Sam  "})
        reply = receive_until(ws, "usage")
        saved = of_type(reply, "message")
        assert [(m["seq"], m["role"], m["text"]) for m in saved] == [
            (2, "user", "My name is Sam"),
            (3, "assistant", "Thanks, Sam."),
        ]
        assert saved[1]["latency_ms"] is not None
        assert not of_type(reply, "audio_frame")  # Text calls never stream audio.
        usage = reply[-1]
        assert usage["user_turns"] == 1
        assert usage["audio_seconds_out"] == 1.0
        assert usage["estimated_cost_usd"] == pytest.approx(0.0003)

        ws.send_json({"type": "end"})
        ended = receive_until(ws, "call_ended")[-1]
        assert ended["reason"] == "caller_ended"

    assert sessions.created[0].sent_text == [KICKOFF_TEXT, "My name is Sam"]

    with db_engine.connect() as conn:
        call = conn.execute(select(Call).where(Call.id == call_id)).one()
        assert (call.status, call.end_reason, call.engine) == ("ended", "caller_ended", "fake")
        assert call.ended_at is not None

        messages = conn.execute(
            select(Message.seq, Message.role, Message.source, Message.text)
            .where(Message.call_id == call_id)
            .order_by(Message.seq)
        ).all()
        assert [tuple(m) for m in messages] == [
            (1, "assistant", "transcription", GREETING),
            (2, "user", "typed", "My name is Sam"),
            (3, "assistant", "transcription", "Thanks, Sam."),
        ]

        usage_rows = conn.execute(select(UsageLog).where(UsageLog.call_id == call_id)).all()
        assert sum(row.audio_seconds_out for row in usage_rows) == 1.0
        assert sum(row.estimated_cost_usd for row in usage_rows) == Decimal("0.000300")
        assert any(row.total_tokens == 30 for row in usage_rows)

        audit = conn.execute(
            select(AuditLog.event, AuditLog.payload)
            .where(AuditLog.call_id == call_id)
            .order_by(AuditLog.id)
        ).all()
        events = [row.event for row in audit]
        assert events[:2] == ["call.started", "call.consent"]
        assert events.count("message.user") == 1
        assert events.count("message.assistant") == 2
        assert events[-1] == "call.ended"
        # Audit payloads carry ids and counts, never message text.
        assert all("Sam" not in str(row.payload) for row in audit)


def test_start_requires_consent(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client(sessions=FakeSessions([]))
    before = count_calls(db_engine)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START | {"consent": False})
        error = ws.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "consent_required"
    assert count_calls(db_engine) == before


def test_gemini_without_api_key_reports_not_configured(
    make_client: AppFactory, db_engine: Engine
) -> None:
    client = make_client()  # Default factory, no GEMINI_API_KEY.
    before = count_calls(db_engine)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START | {"engine": "gemini"})
        error = ws.receive_json()

    assert error["code"] == "not_configured"
    assert count_calls(db_engine) == before


def test_daily_cap_blocks_new_calls(make_client: AppFactory, db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        calls_today = conn.scalar(
            select(func.count())
            .select_from(Call)
            .where(Call.started_at >= start_of_today(), not_(OFFLINE_EVALUATION))
        )
    client = make_client(sessions=FakeSessions([]), daily_call_cap=calls_today or 0)
    before = count_calls(db_engine)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        ended = ws.receive_json()

    assert ended["type"] == "call_ended"
    assert ended["reason"] == "daily_cap"
    assert count_calls(db_engine) == before
    with db_engine.connect() as conn:
        last = conn.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).one()
    assert last.event == "call.rejected"
    assert last.payload["reason"] == "daily_cap"


def test_max_turns_ends_call_after_the_reply(make_client: AppFactory, db_engine: Engine) -> None:
    sessions = FakeSessions([scripted_reply(GREETING), scripted_reply("Nice to meet you.")])
    client = make_client(sessions=sessions, max_turns=1)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        call_id = uuid.UUID(receive_until(ws, "call_started")[-1]["call_id"])
        receive_until(ws, "turn_complete")
        ws.send_json({"type": "user_text", "text": "Hello"})
        messages = receive_until(ws, "call_ended")

    assert of_type(messages, "message")[-1]["text"] == "Nice to meet you."
    assert messages[-1]["reason"] == "max_turns"
    with db_engine.connect() as conn:
        assert conn.scalar(select(Call.end_reason).where(Call.id == call_id)) == "max_turns"


def test_max_call_seconds_ends_call_and_keeps_partial_reply(
    make_client: AppFactory, db_engine: Engine
) -> None:
    # The greeting never completes, so the one-second cap ends the call mid-reply.
    sessions = FakeSessions([[OutputTranscript(text="Hi, I'm Maya and")]])
    client = make_client(sessions=sessions, max_call_seconds=1)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        call_id = uuid.UUID(receive_until(ws, "call_started")[-1]["call_id"])
        messages = receive_until(ws, "call_ended")

    assert messages[-1]["reason"] == "max_duration"
    partial = of_type(messages, "message")[-1]
    assert (partial["text"], partial["interrupted"]) == ("Hi, I'm Maya and", True)
    with db_engine.connect() as conn:
        assert conn.scalar(select(Call.end_reason).where(Call.id == call_id)) == "max_duration"


def test_quota_error_ends_call_with_friendly_message(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions([[LiveQuotaError("429 RESOURCE_EXHAUSTED")]])
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        call_id = uuid.UUID(receive_until(ws, "call_started")[-1]["call_id"])
        ended = receive_until(ws, "call_ended")[-1]

    assert ended["reason"] == "quota"
    assert "quota" in ended["message"]
    assert "RESOURCE_EXHAUSTED" not in ended["message"]
    with db_engine.connect() as conn:
        call = conn.execute(select(Call).where(Call.id == call_id)).one()
        events = conn.scalars(select(AuditLog.event).where(AuditLog.call_id == call_id)).all()
    assert (call.status, call.end_reason) == ("failed", "quota")
    assert "live.quota_error" in events


def test_interruption_saves_partial_reply(make_client: AppFactory) -> None:
    sessions = FakeSessions([[OutputTranscript(text="Hello, the"), Interrupted(), TurnComplete()]])
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        messages = receive_until(ws, "turn_complete")

    assert [m["type"] for m in messages if m["type"] != "assistant_delta"] == [
        "call_started",
        "latency",
        "message",
        "interrupted",
        "usage",
        "turn_complete",
    ]
    assert of_type(messages, "message")[0]["interrupted"] is True


def test_invalid_messages_are_rejected_without_ending_the_call(make_client: AppFactory) -> None:
    sessions = FakeSessions([scripted_reply(GREETING), scripted_reply("Got it.")])
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START)
        receive_until(ws, "turn_complete")

        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_json({"type": "user_text", "text": "x" * 1001})
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_json({"type": "user_text", "text": "   "})
        assert ws.receive_json()["code"] == "invalid_message"
        ws.send_bytes(b"\x00\x01")  # Audio in a text call.
        assert ws.receive_json()["code"] == "invalid_message"

        ws.send_json({"type": "user_text", "text": "Still here"})
        saved = of_type(receive_until(ws, "turn_complete"), "message")
        assert [m["text"] for m in saved] == ["Still here", "Got it."]

    assert sessions.created[0].sent_text == [KICKOFF_TEXT, "Still here"]


def test_websocket_rejects_unknown_origin(make_client: AppFactory) -> None:
    client = make_client(sessions=FakeSessions([]))

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/ws/call", headers={"origin": "https://evil.example"}),
    ):
        pass

    assert exc_info.value.code == 1008


def test_websocket_accepts_frontend_origin(make_client: AppFactory) -> None:
    client = make_client(sessions=FakeSessions([scripted_reply(GREETING)]))

    with client.websocket_connect("/ws/call", headers={"origin": "http://localhost:3000"}) as ws:
        ws.send_json(START)
        assert receive_until(ws, "call_started")[-1]["engine"] == "fake"
