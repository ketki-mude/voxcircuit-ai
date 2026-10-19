"""Audio calls over WS /ws/call, driven by scripted FakeLiveSessions."""

import uuid
from array import array

import pytest
from sqlalchemy import Engine, func, select

from app.live.events import (
    AudioChunk,
    InputTranscript,
    Interrupted,
    OutputTranscript,
    TurnComplete,
)
from app.live.fake import ScriptStep
from app.models import Call, Message
from app.prompts import KICKOFF_TEXT
from tests.conftest import START_AUDIO, AppFactory, FakeSessions, of_type, receive_until

TENTH_OF_A_SECOND_OUT = AudioChunk(data=bytes(4_800))  # 24 kHz, 16-bit
ONE_SECOND_OUT = AudioChunk(data=bytes(48_000))
GREETING: list[ScriptStep] = [
    TENTH_OF_A_SECOND_OUT,
    OutputTranscript(text="Hi, I'm Maya."),
    TurnComplete(),
]


def voiced(ms: int) -> bytes:
    """Caller audio loud enough to count as speech: a 400 Hz square wave at 16 kHz."""
    return array("h", [3000 if (i // 20) % 2 else -3000 for i in range(16 * ms)]).tobytes()


def test_audio_call_relays_audio_and_stores_transcripts(
    make_client: AppFactory, db_engine: Engine
) -> None:
    sessions = FakeSessions(
        [
            GREETING,
            [
                InputTranscript(text="My name "),
                InputTranscript(text="is Sam.", finished=True),
                ONE_SECOND_OUT,
                OutputTranscript(text="Thanks, Sam."),
                TurnComplete(),
            ],
        ],
        audio_turn_bytes=3_200,
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START_AUDIO)
        call_id = uuid.UUID(receive_until(ws, "call_started")[-1]["call_id"])
        greeting = receive_until(ws, "turn_complete")
        assert [len(m["data"]) for m in of_type(greeting, "audio_frame")] == [4_800]
        assert of_type(greeting, "latency")

        ws.send_bytes(voiced(100))  # 3,200 bytes: the fake treats it as one utterance.
        reply = receive_until(ws, "turn_complete")
        assert [m["type"] for m in reply] == [
            "user_delta",
            "user_delta",
            "message",
            "latency",
            "audio_frame",
            "assistant_delta",
            "message",
            "usage",
            "turn_complete",
        ]
        saved = of_type(reply, "message")
        assert [(m["role"], m["text"]) for m in saved] == [
            ("user", "My name is Sam."),
            ("assistant", "Thanks, Sam."),
        ]
        latency = of_type(reply, "latency")[0]["latency_ms"]
        assert saved[1]["latency_ms"] == latency == reply[-1]["latency_ms"]
        usage = of_type(reply, "usage")[0]
        assert (usage["audio_seconds_in"], usage["audio_seconds_out"]) == (0.1, 1.1)
        assert usage["user_turns"] == 1
        assert usage["estimated_cost_usd"] == pytest.approx(0.000338)

        ws.send_json({"type": "end"})
        assert receive_until(ws, "call_ended")[-1]["reason"] == "caller_ended"

    assert sessions.created[0].sent_audio_bytes == 3_200
    assert sessions.created[0].sent_text == [KICKOFF_TEXT]
    with db_engine.connect() as conn:
        assert conn.scalar(select(Call.input_mode).where(Call.id == call_id)) == "audio"
        rows = conn.execute(
            select(Message.seq, Message.role, Message.source, Message.text, Message.latency_ms)
            .where(Message.call_id == call_id)
            .order_by(Message.seq)
        ).all()
    assert [(r.role, r.source, r.text) for r in rows] == [
        ("assistant", "transcription", "Hi, I'm Maya."),
        ("user", "transcription", "My name is Sam."),
        ("assistant", "transcription", "Thanks, Sam."),
    ]
    assert rows[2].latency_ms == latency


def test_caller_transcript_is_saved_before_the_reply_without_finished_flag(
    make_client: AppFactory,
) -> None:
    sessions = FakeSessions(
        [
            GREETING,
            [
                InputTranscript(text="Hello"),
                InputTranscript(text=" there"),
                ONE_SECOND_OUT,
                OutputTranscript(text="Hi."),
                TurnComplete(),
            ],
        ],
        audio_turn_bytes=3_200,
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START_AUDIO)
        receive_until(ws, "turn_complete")
        ws.send_bytes(voiced(100))
        saved = of_type(receive_until(ws, "turn_complete"), "message")

    assert [(m["seq"], m["role"], m["text"]) for m in saved] == [
        (2, "user", "Hello there"),
        (3, "assistant", "Hi."),
    ]


def test_barge_in_clears_the_reply_and_keeps_the_partial_transcript(
    make_client: AppFactory,
) -> None:
    sessions = FakeSessions(
        [
            GREETING,
            [
                InputTranscript(text="Tell me about coverage.", finished=True),
                ONE_SECOND_OUT,
                OutputTranscript(text="I can set up a call with"),
                Interrupted(),
                InputTranscript(text="Wait.", finished=True),
                TENTH_OF_A_SECOND_OUT,
                OutputTranscript(text="Sure."),
                TurnComplete(),
            ],
        ],
        audio_turn_bytes=3_200,
    )
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START_AUDIO)
        receive_until(ws, "turn_complete")
        ws.send_bytes(voiced(100))
        frames = receive_until(ws, "turn_complete")

    kinds = [m["type"] for m in frames]
    # The interruption notice follows the audio it cancels, so the client can clear it.
    assert kinds.index("interrupted") > kinds.index("audio_frame")
    saved = of_type(frames, "message")
    assert [(m["role"], m["text"], m["interrupted"]) for m in saved] == [
        ("user", "Tell me about coverage.", False),
        ("assistant", "I can set up a call with", True),
        ("user", "Wait.", False),
        ("assistant", "Sure.", False),
    ]


def test_invalid_audio_frames_are_rejected(make_client: AppFactory) -> None:
    sessions = FakeSessions([GREETING])
    client = make_client(sessions=sessions)

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START_AUDIO)
        receive_until(ws, "turn_complete")
        for frame in (b"\x00\x01\x02", bytes(32_002), b""):
            ws.send_bytes(frame)
            assert ws.receive_json()["code"] == "invalid_message"
        ws.send_bytes(voiced(40))
        ws.send_json({"type": "end"})
        receive_until(ws, "call_ended")

    assert sessions.created[0].sent_audio_bytes == len(voiced(40))


def test_offline_engine_refuses_voice_calls(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client()  # Default factory.
    with db_engine.connect() as conn:
        before = conn.scalar(select(func.count()).select_from(Call))

    with client.websocket_connect("/ws/call") as ws:
        ws.send_json(START_AUDIO)
        error = ws.receive_json()

    assert error["code"] == "not_configured"
    assert "text mode only" in error["message"]
    with db_engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(Call)) == before
