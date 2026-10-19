"""Messages exchanged over WS /ws/call. Mirrored in frontend/src/lib/call-protocol.ts.

JSON text frames carry the messages below. In an audio call, binary frames carry raw
16-bit little-endian mono PCM: caller audio at 16 kHz (client to server) and Maya's
audio at 24 kHz (server to client).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.booking.schemas import Action, StepReport
from app.live.factory import EngineName, InputMode
from app.safety.handoff import HandoffReason
from app.verification.types import CheckStatus

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
    "handed_off",
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
    session_token: str  # Bearer token for this call's REST endpoints (approvals).


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


class ToolEvent(BaseModel):
    """The backend ran one of the model's tools. Drives the circuit timeline."""

    type: Literal["tool"] = "tool"
    name: str
    ok: bool
    summary: str  # No personal details.


class TicketRow(BaseModel):
    label: str
    value: str


class ApprovalRequested(BaseModel):
    """Show the caller a booking ticket to approve or cancel."""

    type: Literal["approval_requested"] = "approval_requested"
    approval_id: UUID
    action: Action
    rows: list[TicketRow]
    expires_at: datetime


class ApprovalResolvedMessage(BaseModel):
    type: Literal["approval_resolved"] = "approval_resolved"
    approval_id: UUID
    status: Literal["approved", "cancelled", "expired"]


class BookingResult(BaseModel):
    type: Literal["booking_result"] = "booking_result"
    approval_id: UUID
    success: bool
    summary: str
    steps: list[StepReport]


class VerificationStartedMessage(BaseModel):
    """Demo only: the code a real system would text to the caller's phone."""

    type: Literal["verification_started"] = "verification_started"
    challenge_id: UUID
    masked_email: str
    demo_code: str
    expires_at: datetime


class VerificationResultMessage(BaseModel):
    type: Literal["verification_result"] = "verification_result"
    status: CheckStatus
    attempts_left: int
    returning_customer: bool


class HandoffMessage(BaseModel):
    type: Literal["handoff"] = "handoff"
    reason: HandoffReason
    label: str


class DataDeletedMessage(BaseModel):
    type: Literal["data_deleted"] = "data_deleted"


class HandoffTakenMessage(BaseModel):
    type: Literal["handoff_taken"] = "handoff_taken"
    agent_name: str


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
    | ToolEvent
    | ApprovalRequested
    | ApprovalResolvedMessage
    | BookingResult
    | VerificationStartedMessage
    | VerificationResultMessage
    | HandoffMessage
    | DataDeletedMessage
    | HandoffTakenMessage
    | CallEnded
    | ErrorNotice
)
