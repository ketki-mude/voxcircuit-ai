"""Provider-neutral events emitted by a LiveSession.

GeminiLiveSession translates Live API server messages into these, and FakeLiveSession
replays them from a script, so the call handler never touches SDK types.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OUTPUT_SAMPLE_RATE = 24_000
INPUT_SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # 16-bit PCM, mono


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class InputTranscript(_Event):
    """Incremental transcription of the caller's speech."""

    type: Literal["input_transcript"] = "input_transcript"
    text: str
    finished: bool = False  # True on the last fragment of the caller's utterance.


class OutputTranscript(_Event):
    """Incremental transcription of the model's speech. This is the model's reply as text."""

    type: Literal["output_transcript"] = "output_transcript"
    text: str
    finished: bool = False


class AudioChunk(_Event):
    """Model speech: 16-bit PCM, 24 kHz, mono."""

    type: Literal["audio"] = "audio"
    data: bytes

    @property
    def seconds(self) -> float:
        return len(self.data) / (OUTPUT_SAMPLE_RATE * BYTES_PER_SAMPLE)


class Interrupted(_Event):
    """The caller barged in; any queued model audio must be dropped."""

    type: Literal["interrupted"] = "interrupted"


class TurnComplete(_Event):
    type: Literal["turn_complete"] = "turn_complete"


class Usage(_Event):
    type: Literal["usage"] = "usage"
    prompt_tokens: int | None = None
    response_tokens: int | None = None
    total_tokens: int | None = None


class GoAway(_Event):
    """The server will close this connection soon; the session reconnects on its own."""

    type: Literal["go_away"] = "go_away"
    time_left_seconds: float | None = None


class FunctionCall(_Event):
    id: str
    name: str
    args: dict[str, Any]


class ToolCall(_Event):
    """The model wants these functions run. It waits for the responses (blocking tools)."""

    type: Literal["tool_call"] = "tool_call"
    calls: tuple[FunctionCall, ...]


class ToolCallCancelled(_Event):
    """The model no longer needs these calls' results (for example, after a barge-in)."""

    type: Literal["tool_call_cancelled"] = "tool_call_cancelled"
    ids: tuple[str, ...]


class SessionResumed(_Event):
    """The session reconnected using its resumption handle."""

    type: Literal["session_resumed"] = "session_resumed"


LiveEvent = Annotated[
    InputTranscript
    | OutputTranscript
    | AudioChunk
    | Interrupted
    | TurnComplete
    | Usage
    | GoAway
    | ToolCall
    | ToolCallCancelled
    | SessionResumed,
    Field(discriminator="type"),
]
