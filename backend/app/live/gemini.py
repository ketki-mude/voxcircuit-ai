import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import AsyncExitStack, aclosing
from typing import cast

from google import genai
from google.genai import types
from google.genai.live import AsyncSession

from app.live.base import (
    FunctionSpec,
    LiveConfigError,
    LiveQuotaError,
    LiveSession,
    LiveSessionError,
    ToolResponse,
)
from app.live.events import (
    INPUT_SAMPLE_RATE,
    AudioChunk,
    FunctionCall,
    GoAway,
    InputTranscript,
    Interrupted,
    LiveEvent,
    OutputTranscript,
    SessionResumed,
    ToolCall,
    ToolCallCancelled,
    TurnComplete,
    Usage,
)

log = logging.getLogger(__name__)

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")
_QUOTA_MARKERS = ("resource_exhausted", "quota", "rate limit")
_CONFIG_MARKERS = ("api key", "api_key_invalid", "permission_denied", "not found")
_CONFIG_CODES = frozenset({400, 401, 403, 404, 1007, 1008})


def parse_duration(value: str | None) -> float | None:
    """Parse a protobuf duration string such as "10s" or "1.5s"."""
    if not value:
        return None
    match = _DURATION.match(value)
    return float(match.group(1)) if match else None


def classify_error(exc: BaseException) -> LiveSessionError:
    """Map SDK, websocket and timeout errors onto the LiveSession error types."""
    if isinstance(exc, LiveSessionError):
        return exc
    if isinstance(exc, TimeoutError):
        return LiveSessionError("timed out talking to the Live API")
    detail = str(exc) or type(exc).__name__
    lowered = detail.lower()
    code = getattr(exc, "code", None)
    if code is None:
        # websockets.InvalidStatus carries the HTTP response of a rejected handshake.
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code == 429 or any(marker in lowered for marker in _QUOTA_MARKERS):
        return LiveQuotaError(detail)
    if code in _CONFIG_CODES or any(marker in lowered for marker in _CONFIG_MARKERS):
        return LiveConfigError(detail)
    return LiveSessionError(detail)


def translate_message(message: types.LiveServerMessage) -> list[LiveEvent]:
    """Convert one Live API server message into provider-neutral events."""
    events: list[LiveEvent] = []
    content = message.server_content
    if content is not None:
        heard = content.input_transcription
        if heard is not None and (heard.text or heard.finished):
            events.append(InputTranscript(text=heard.text or "", finished=bool(heard.finished)))
        if content.model_turn and content.model_turn.parts:
            for part in content.model_turn.parts:
                # Native audio models reply in audio only; text parts are thoughts.
                if part.inline_data and part.inline_data.data and not part.thought:
                    events.append(AudioChunk(data=part.inline_data.data))
        said = content.output_transcription
        if said is not None and (said.text or said.finished):
            events.append(OutputTranscript(text=said.text or "", finished=bool(said.finished)))
        if content.interrupted:
            events.append(Interrupted())
        if content.turn_complete:
            events.append(TurnComplete())
    if message.usage_metadata is not None:
        usage = message.usage_metadata
        events.append(
            Usage(
                prompt_tokens=usage.prompt_token_count,
                response_tokens=usage.response_token_count,
                total_tokens=usage.total_token_count,
            )
        )
    if message.tool_call is not None and message.tool_call.function_calls:
        calls = tuple(
            FunctionCall(id=fc.id or "", name=fc.name or "", args=dict(fc.args or {}))
            for fc in message.tool_call.function_calls
        )
        events.append(ToolCall(calls=calls))
    cancelled = message.tool_call_cancellation
    if cancelled is not None and cancelled.ids:
        events.append(ToolCallCancelled(ids=tuple(cancelled.ids)))
    if message.go_away is not None:
        events.append(GoAway(time_left_seconds=parse_duration(message.go_away.time_left)))
    return events


def function_declaration(spec: FunctionSpec) -> types.FunctionDeclaration:
    """Declare a function with its Pydantic-derived JSON schema as a Gemini Schema."""
    schema = (
        types.Schema.from_json_schema(
            json_schema=types.JSONSchema.model_validate(spec.parameters),
            raise_error_on_unsupported_field=True,
        )
        if spec.parameters.get("properties")
        else None  # A function without arguments declares no parameters.
    )
    # No behavior is set: calls are blocking (the default), so the model waits for results.
    return types.FunctionDeclaration(
        name=spec.name, description=spec.description, parameters=schema
    )


class GeminiLiveSession(LiveSession):
    """LiveSession backed by the Gemini Live API (google-genai, client.aio.live.connect).

    Replies are always requested as AUDIO with output transcription enabled, because
    native audio models do not support the TEXT response modality. Text mode reads the
    transcription and ignores the audio.

    Reconnects are transparent: the session keeps the latest resumption handle, and on a
    go-away (or a dropped connection) it reconnects with that handle and yields
    SessionResumed. While it reconnects, caller audio is dropped (stale audio is useless)
    and a text turn waits for the new connection. Context window compression keeps long
    calls inside the context limit.
    """

    def __init__(
        self,
        *,
        client: genai.Client,
        model: str,
        system_instruction: str,
        silence_duration_ms: int,
        functions: Sequence[FunctionSpec] = (),
        connect_timeout_s: float = 15.0,
        max_reconnects: int = 3,
    ) -> None:
        self._client = client
        self._tools = (
            [types.Tool(function_declarations=[function_declaration(f) for f in functions])]
            if functions
            else None
        )
        self._model = model
        self._system_instruction = system_instruction
        self._silence_duration_ms = silence_duration_ms
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnects = max_reconnects
        self._handle: str | None = None
        self._stack: AsyncExitStack | None = None
        self._session: AsyncSession | None = None
        self._ready = asyncio.Event()
        self._closing = False
        self._turn_active = False

    @property
    def resumption_handle(self) -> str | None:
        return self._handle

    def build_config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=self._system_instruction,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    silence_duration_ms=self._silence_duration_ms,
                ),
            ),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            # handle=None starts a new resumable session; a handle resumes the old one.
            session_resumption=types.SessionResumptionConfig(handle=self._handle),
            tools=self._tools,
        )

    async def connect(self) -> None:
        await self._open()

    async def send_text(self, text: str) -> None:
        turn = types.Content(role="user", parts=[types.Part(text=text)])
        session = await self._current()
        try:
            await session.send_client_content(turns=turn, turn_complete=True)
            return
        except Exception as exc:
            error = classify_error(exc)
            if not self._can_resume(error):
                raise error from exc
        # The connection dropped under us: wait for events() to resume it, then retry once.
        session = await self._next_connection(session)
        try:
            await session.send_client_content(turns=turn, turn_complete=True)
        except Exception as exc:
            raise classify_error(exc) from exc

    async def send_audio(self, pcm: bytes) -> None:
        if self._closing:
            raise LiveSessionError("Live API session is closed")
        session = self._session
        if session is None or not self._ready.is_set():
            return  # Reconnecting: drop the frame rather than queue stale audio.
        try:
            await session.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={INPUT_SAMPLE_RATE}")
            )
        except Exception as exc:
            error = classify_error(exc)
            if not self._can_resume(error):
                raise error from exc

    async def send_tool_responses(self, responses: Sequence[ToolResponse]) -> None:
        session = await self._current()
        try:
            await session.send_tool_response(
                function_responses=[
                    types.FunctionResponse(id=r.id, name=r.name, response=r.response)
                    for r in responses
                ]
            )
        except Exception as exc:
            error = classify_error(exc)
            if not self._can_resume(error):
                raise error from exc
            log.warning("Tool response lost while the session reconnects: %s", error)

    async def events(self) -> AsyncIterator[LiveEvent]:
        failures = 0
        while not self._closing:
            try:
                session = await self._current()
            except LiveSessionError:
                if self._closing:
                    return
                raise
            reconnect = False
            try:
                # receive() ends after each complete model turn, so loop around it.
                # It is an async generator at runtime; the SDK types it as AsyncIterator.
                stream = cast(AsyncGenerator[types.LiveServerMessage, None], session.receive())
                async with aclosing(stream):
                    async for message in stream:
                        failures = 0
                        self._track(message)
                        for event in translate_message(message):
                            yield event
                        if message.go_away is not None:
                            reconnect = True
                            if not self._turn_active:
                                break  # Idle: reconnect now. Mid-turn: after the turn ends.
            except Exception as exc:
                if self._closing:
                    return
                error = classify_error(exc)
                resumable = self._handle is not None and failures < self._max_reconnects
                if isinstance(error, LiveQuotaError | LiveConfigError) or not resumable:
                    raise error from exc
                log.warning("Live API connection dropped; resuming session: %s", error)
                failures += 1
                reconnect = True
            if reconnect and not self._closing:
                await self._drop_connection()
                await self._open()
                yield SessionResumed()

    async def close(self) -> None:
        self._closing = True
        await self._drop_connection()
        self._ready.set()  # Wake anyone waiting in _current() so they see the session closed.

    async def _open(self) -> None:
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self._connect_timeout_s):
                session = await stack.enter_async_context(
                    self._client.aio.live.connect(model=self._model, config=self.build_config())
                )
        except Exception as exc:
            await stack.aclose()
            raise classify_error(exc) from exc
        self._stack, self._session = stack, session
        self._turn_active = False
        self._ready.set()

    async def _drop_connection(self) -> None:
        self._ready.clear()
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def _current(self) -> AsyncSession:
        if not self._closing and not self._ready.is_set():
            try:
                async with asyncio.timeout(self._connect_timeout_s):
                    await self._ready.wait()
            except TimeoutError as exc:
                raise LiveSessionError("Live API session is not connected") from exc
        if self._closing or self._session is None:
            raise LiveSessionError("Live API session is closed")
        return self._session

    def _can_resume(self, error: LiveSessionError) -> bool:
        return (
            not self._closing
            and self._handle is not None
            and not isinstance(error, LiveQuotaError | LiveConfigError)
        )

    async def _next_connection(self, dropped: AsyncSession) -> AsyncSession:
        """Wait until events() has replaced the dropped connection with a resumed one."""
        try:
            async with asyncio.timeout(self._connect_timeout_s):
                while True:
                    if self._closing:
                        raise LiveSessionError("Live API session is closed")
                    session = self._session
                    if session is not None and session is not dropped and self._ready.is_set():
                        return session
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise LiveSessionError("Live API session did not reconnect") from exc

    def _track(self, message: types.LiveServerMessage) -> None:
        update = message.session_resumption_update
        if update is not None and update.resumable and update.new_handle:
            self._handle = update.new_handle
        content = message.server_content
        if content is not None:
            if content.model_turn is not None or content.output_transcription is not None:
                self._turn_active = True
            if content.turn_complete or content.interrupted:
                self._turn_active = False
