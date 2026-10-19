"""Messages exchanged over WS /ws/call. Mirrored in frontend/src/lib/call-protocol.ts.

JSON text frames carry the messages below. In an audio call, binary frames carry raw
16-bit little-endian mono PCM: caller audio at 16 kHz (client to server) and Maya's
audio at 24 kHz (server to client).
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.live.factory import EngineName, InputMode

MAX_USER_TEXT_CHARS = 1000
MAX_AUDIO_FRAME_BYTES = 32_000  # One second of 16 kHz caller audio.
CONSENT_NOTICE = "This demo uses an AI voice and transcribes the call. Use fake details only."

EndReason = Literal[
    "caller_ended",
    "disconnected",
    "max_duration",
    "max_turns",
    "daily_cap",
    "quota",
    "error",
]
ErrorCode = Literal["invalid_message", "consent_required", "not_configured"]


# Client -> server


class _ClientMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartCall(_ClientMessage):
    type: Literal["start"]
    engine: EngineName = "gemini"
    input_mode: InputMode = "text"
    consent: bool = False


class UserText(_ClientMessage):
    type: Literal["user_text"]
    text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_USER_TEXT_CHARS),
    ]


class EndCall(_ClientMessage):
    type: Literal["end"]


ClientMessage = Annotated[StartCall | UserText | EndCall, Field(discriminator="type")]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# Server -> client


class CallStarted(BaseModel):
    type: Literal["call_started"] = "call_started"
    call_id: UUID
    engine: EngineName
    max_call_seconds: int
    max_turns: int


class UserDelta(BaseModel):
    """A fragment of the caller's speech as it is transcribed. Not yet saved."""

    type: Literal["user_delta"] = "user_delta"
    text: str


class AssistantDelta(BaseModel):
    """A fragment of Maya's reply as it is transcribed. Not yet saved."""

    type: Literal["assistant_delta"] = "assistant_delta"
    text: str


class MessageSaved(BaseModel):
    """A finalized turn that is now stored in the messages table."""

    type: Literal["message"] = "message"
    id: int
    seq: int
    role: Literal["user", "assistant"]
    text: str
    interrupted: bool
    latency_ms: int | None


class InterruptedNotice(BaseModel):
    type: Literal["interrupted"] = "interrupted"


class LatencyMeasured(BaseModel):
    """Caller's last input (voiced audio frame or typed turn) -> Maya's first audio."""

    type: Literal["latency"] = "latency"
    latency_ms: int


class TurnCompleted(BaseModel):
    type: Literal["turn_complete"] = "turn_complete"
    latency_ms: int | None


class UsageUpdate(BaseModel):
    type: Literal["usage"] = "usage"
    elapsed_seconds: float
    user_turns: int
    audio_seconds_in: float
    audio_seconds_out: float
    estimated_cost_usd: float


class CallEnded(BaseModel):
    type: Literal["call_ended"] = "call_ended"
    reason: EndReason
    message: str


class ErrorNotice(BaseModel):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str


ServerMessage = (
    CallStarted
    | UserDelta
    | AssistantDelta
    | LatencyMeasured
    | MessageSaved
    | InterruptedNotice
    | TurnCompleted
    | UsageUpdate
    | CallEnded
    | ErrorNotice
)
