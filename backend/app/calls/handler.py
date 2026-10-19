import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Coroutine
from typing import Any, Literal

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.types import Message as AsgiMessage
from starlette.websockets import WebSocketState

from app.calls import protocol as proto
from app.calls.latency import LatencyTracker
from app.calls.recorder import CallRecorder
from app.calls.usage import UsageMeter
from app.config import Settings
from app.live import events as ev
from app.live.audio import INPUT_BYTES_PER_SECOND, is_speech
from app.live.base import LiveConfigError, LiveQuotaError, LiveSession, LiveSessionError
from app.live.events import BYTES_PER_SAMPLE
from app.live.factory import InputMode, LiveSessionFactory
from app.prompts import KICKOFF_TEXT

log = logging.getLogger(__name__)

START_TIMEOUT_S = 30.0

NOT_CONFIGURED_MESSAGE = (
    "The AI model is not configured on the server. Add GEMINI_API_KEY to backend/.env, "
    "or use the offline test engine."
)
END_MESSAGES: dict[proto.EndReason, str] = {
    "caller_ended": "Call ended.",
    "disconnected": "Call ended.",
    "max_duration": "This demo call reached its time limit, so it has ended.",
    "max_turns": "This demo call reached its message limit, so it has ended.",
    "daily_cap": "Today's demo call limit has been reached. Please try again tomorrow.",
    "quota": "The AI service's free quota is used up for now. Please try again later.",
    "error": "Something went wrong with the AI connection, so the call has ended.",
}
FAILED_REASONS: frozenset[proto.EndReason] = frozenset({"quota", "error"})


class CallHandler:
    """Runs one call on WS /ws/call: browser <-> backend <-> LiveSession.

    The browser sends a start message (with consent), then typed turns and, in an audio
    call, binary caller audio. The backend relays Maya's audio back, stores each finalized
    turn (typed text or transcript, never audio), meters usage, measures latency and
    enforces the call caps.
    """

    def __init__(
        self,
        *,
        websocket: WebSocket,
        settings: Settings,
        recorder: CallRecorder,
        session_factory: LiveSessionFactory,
    ) -> None:
        self._ws = websocket
        self._settings = settings
        self._recorder = recorder
        self._session_factory = session_factory
        self._call_id: uuid.UUID | None = None
        self._input_mode: InputMode = "text"
        self._client_gone = False
        self._seq = 0
        self._user_turns = 0
        self._started_at = 0.0
        self._heard: list[str] = []  # Caller speech transcribed but not yet saved.
        self._reply: list[str] = []  # Maya's reply transcribed but not yet saved.
        self._reply_open = False
        self._reply_latency_ms: int | None = None
        self._latency = LatencyTracker(audio_only=False)
        self._usage = UsageMeter(settings.audio_cost_per_min_in, settings.audio_cost_per_min_out)

    async def run(self) -> None:
        await self._ws.accept()
        start = await self._receive_start()
        if start is None:
            await self._close()
            return
        try:
            session = self._session_factory(start.engine, start.input_mode)
        except LiveConfigError as exc:
            log.warning("Cannot start %s session: %s", start.engine, exc)
            await self._send(
                proto.ErrorNotice(
                    code="not_configured", message=exc.caller_message or NOT_CONFIGURED_MESSAGE
                )
            )
            await self._close()
            return

        self._input_mode = start.input_mode
        self._latency = LatencyTracker(audio_only=start.input_mode == "audio")
        call_id = await self._recorder.start_call(
            engine=start.engine,
            input_mode=start.input_mode,
            model=self._settings.gemini_live_model if start.engine == "gemini" else None,
            daily_cap=self._settings.daily_call_cap,
        )
        if call_id is None:
            await self._send(proto.CallEnded(reason="daily_cap", message=END_MESSAGES["daily_cap"]))
            await self._close()
            return
        self._call_id = call_id
        await self._recorder.audit(
            call_id, "caller", "call.consent", {"notice": proto.CONSENT_NOTICE}
        )
        reason = await self._run_call(session, start)
        await self._finish(reason)

    async def _run_call(self, session: LiveSession, start: proto.StartCall) -> proto.EndReason:
        try:
            async with session:
                self._started_at = time.monotonic()
                await self._send(
                    proto.CallStarted(
                        call_id=self._require_call_id(),
                        engine=start.engine,
                        max_call_seconds=self._settings.max_call_seconds,
                        max_turns=self._settings.max_turns,
                    )
                )
                await self._send_to_model(session, KICKOFF_TEXT)
                return await self._race(
                    self._pump_client(session), self._pump_model(session), self._call_timer()
                )
        except LiveQuotaError as exc:
            log.warning("Call %s hit a quota limit: %s", self._call_id, exc)
            await self._recorder.audit(self._call_id, "system", "live.quota_error")
            return "quota"
        except LiveSessionError as exc:
            log.warning("Call %s live session failed: %s", self._call_id, exc)
            await self._recorder.audit(
                self._call_id, "system", "live.error", {"error_type": type(exc).__name__}
            )
            return "error"
        except Exception:
            log.exception("Call %s failed", self._call_id)
            return "error"

    async def _race(self, *jobs: Coroutine[Any, Any, proto.EndReason]) -> proto.EndReason:
        """Run the jobs until the first one finishes; its result (or error) ends the call."""
        tasks = [asyncio.create_task(job) for job in jobs]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
        # Use wait, not gather: if this task is cancelled meanwhile, gather would re-raise a
        # job's CancelledError in place of ours and the server could not recognize it.
        await asyncio.wait(tasks)
        failed = [task for task in done if task.exception() is not None]
        return (failed or list(done))[0].result()

    async def _pump_client(self, session: LiveSession) -> proto.EndReason:
        while True:
            raw = await self._ws.receive()
            if raw["type"] == "websocket.disconnect":
                self._client_gone = True
                return "disconnected"
            audio = raw.get("bytes")
            if audio is not None:
                await self._on_caller_audio(session, audio)
                continue
            message = await self._parse(raw)
            match message:
                case proto.EndCall():
                    return "caller_ended"
                case proto.StartCall():
                    await self._send(
                        proto.ErrorNotice(
                            code="invalid_message", message="The call has already started."
                        )
                    )
                case proto.UserText(text=text):
                    if self._user_turns >= self._settings.max_turns:
                        return "max_turns"
                    self._user_turns += 1
                    await self._save_message("user", "typed", text)
                    await self._send_to_model(session, text)

    async def _on_caller_audio(self, session: LiveSession, pcm: bytes) -> None:
        if self._input_mode != "audio":
            await self._send(
                proto.ErrorNotice(
                    code="invalid_message", message="This is a text call, so audio is ignored."
                )
            )
            return
        if not pcm or len(pcm) % BYTES_PER_SAMPLE or len(pcm) > proto.MAX_AUDIO_FRAME_BYTES:
            await self._send(
                proto.ErrorNotice(
                    code="invalid_message",
                    message="Audio must be 16-bit PCM frames of at most one second.",
                )
            )
            return
        self._usage.add_audio_in(len(pcm) / INPUT_BYTES_PER_SECOND)
        if is_speech(pcm):
            self._latency.caller_input(time.monotonic())
        await session.send_audio(pcm)

    async def _pump_model(self, session: LiveSession) -> proto.EndReason:
        async for event in session.events():
            match event:
                case ev.InputTranscript(text=text, finished=finished):
                    if text:
                        self._heard.append(text)
                        await self._send(proto.UserDelta(text=text))
                    if finished:
                        await self._finish_caller_turn()
                case ev.OutputTranscript(text=text):
                    await self._on_model_output(is_audio=False)
                    if text:
                        self._reply.append(text)
                        await self._send(proto.AssistantDelta(text=text))
                case ev.AudioChunk(data=data):
                    await self._on_model_output(is_audio=True)
                    self._usage.add_audio_out(event.seconds)
                    if self._input_mode == "audio":
                        await self._send_frame(data)
                case ev.Interrupted():
                    await self._finish_reply(interrupted=True)
                    await self._send(proto.InterruptedNotice())
                case ev.TurnComplete():
                    latency_ms = self._reply_latency_ms
                    await self._finish_caller_turn()
                    await self._finish_reply(interrupted=False)
                    await self._flush_usage()
                    await self._send(proto.TurnCompleted(latency_ms=latency_ms))
                    if self._user_turns >= self._settings.max_turns:
                        return "max_turns"
                case ev.Usage():
                    self._usage.set_tokens(
                        event.prompt_tokens, event.response_tokens, event.total_tokens
                    )
                case ev.GoAway():
                    await self._recorder.audit(
                        self._call_id,
                        "system",
                        "live.go_away",
                        {"time_left_seconds": event.time_left_seconds},
                    )
                case ev.SessionResumed():
                    await self._recorder.audit(self._call_id, "system", "live.resumed")
        log.warning("Call %s: live session ended without being closed", self._call_id)
        return "error"

    async def _call_timer(self) -> proto.EndReason:
        await asyncio.sleep(self._settings.max_call_seconds)
        return "max_duration"

    async def _send_to_model(self, session: LiveSession, text: str) -> None:
        self._latency.caller_input(time.monotonic())
        await session.send_text(text)

    async def _on_model_output(self, *, is_audio: bool) -> None:
        latency_ms = self._latency.model_output(time.monotonic(), is_audio=is_audio)
        if not self._reply_open:
            self._reply_open = True
            # Speech heard before Maya started replying is saved before her reply.
            await self._finish_caller_turn()
        if latency_ms is not None:
            self._reply_latency_ms = latency_ms
            await self._send(proto.LatencyMeasured(latency_ms=latency_ms))

    async def _finish_caller_turn(self) -> None:
        text = "".join(self._heard).strip()
        self._heard.clear()
        if text:
            self._user_turns += 1
            await self._save_message("user", "transcription", text)

    async def _finish_reply(self, *, interrupted: bool) -> None:
        text = "".join(self._reply).strip()
        latency_ms = self._reply_latency_ms
        self._reply.clear()
        self._reply_open = False
        self._reply_latency_ms = None
        self._latency.reply_finished()
        if text:
            await self._save_message(
                "assistant",
                "transcription",
                text,
                interrupted=interrupted,
                latency_ms=latency_ms,
            )

    async def _save_message(
        self,
        role: Literal["user", "assistant"],
        source: Literal["typed", "transcription"],
        text: str,
        *,
        interrupted: bool = False,
        latency_ms: int | None = None,
    ) -> None:
        self._seq += 1
        seq = self._seq
        message_id = await self._recorder.add_message(
            self._require_call_id(),
            seq=seq,
            role=role,
            source=source,
            text=text,
            interrupted=interrupted,
            latency_ms=latency_ms,
        )
        await self._send(
            proto.MessageSaved(
                id=message_id,
                seq=seq,
                role=role,
                text=text,
                interrupted=interrupted,
                latency_ms=latency_ms,
            )
        )

    async def _flush_usage(self) -> None:
        delta = self._usage.take()
        if delta is not None:
            await self._recorder.add_usage(self._require_call_id(), delta)
        await self._send(self._usage_update())

    def _usage_update(self) -> proto.UsageUpdate:
        return proto.UsageUpdate(
            elapsed_seconds=round(time.monotonic() - self._started_at, 1)
            if self._started_at
            else 0.0,
            user_turns=self._user_turns,
            audio_seconds_in=round(self._usage.total_seconds_in, 2),
            audio_seconds_out=round(self._usage.total_seconds_out, 2),
            estimated_cost_usd=float(self._usage.total_cost_usd),
        )

    async def _finish(self, reason: proto.EndReason) -> None:
        call_id = self._require_call_id()
        try:
            # Keep whatever was said when the call ended.
            await self._finish_caller_turn()
            await self._finish_reply(interrupted=True)
            delta = self._usage.take()
            if delta is not None:
                await self._recorder.add_usage(call_id, delta)
        except Exception:
            log.exception("Call %s: could not save the final turn", call_id)
        await self._recorder.end_call(
            call_id,
            failed=reason in FAILED_REASONS,
            reason=reason,
            summary={
                "user_turns": self._user_turns,
                "audio_seconds_in": round(self._usage.total_seconds_in, 2),
                "audio_seconds_out": round(self._usage.total_seconds_out, 2),
                "estimated_cost_usd": str(self._usage.total_cost_usd),
            },
        )
        await self._send(self._usage_update())
        await self._send(proto.CallEnded(reason=reason, message=END_MESSAGES[reason]))
        await self._close()

    async def _receive_start(self) -> proto.StartCall | None:
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                raw = await self._ws.receive()
        except TimeoutError:
            await self._send(
                proto.ErrorNotice(code="invalid_message", message="No start message received.")
            )
            return None
        if raw["type"] == "websocket.disconnect":
            self._client_gone = True
            return None
        message = await self._parse(raw)
        if message is None:
            return None
        if not isinstance(message, proto.StartCall):
            await self._send(
                proto.ErrorNotice(code="invalid_message", message="Start the call first.")
            )
            return None
        if not message.consent:
            await self._send(
                proto.ErrorNotice(
                    code="consent_required",
                    message="Please confirm the notice before starting the call.",
                )
            )
            return None
        return message

    async def _parse(self, raw: AsgiMessage) -> proto.ClientMessage | None:
        text = raw.get("text")
        if not isinstance(text, str):
            await self._send(
                proto.ErrorNotice(code="invalid_message", message="Expected a JSON text message.")
            )
            return None
        try:
            return proto.client_message_adapter.validate_json(text)
        except ValidationError:
            await self._send(
                proto.ErrorNotice(
                    code="invalid_message",
                    message="That message was not understood. Messages must be 1 to "
                    f"{proto.MAX_USER_TEXT_CHARS} characters.",
                )
            )
            return None

    async def _send(self, message: proto.ServerMessage) -> None:
        await self._send_frame(message.model_dump_json())

    async def _send_frame(self, frame: str | bytes) -> None:
        if self._client_gone:
            return
        try:
            if isinstance(frame, bytes):
                await self._ws.send_bytes(frame)
            else:
                await self._ws.send_text(frame)
        except (WebSocketDisconnect, RuntimeError, OSError):
            self._client_gone = True

    async def _close(self) -> None:
        if self._client_gone or self._ws.application_state == WebSocketState.DISCONNECTED:
            return
        with contextlib.suppress(RuntimeError, OSError):
            await self._ws.close()

    def _require_call_id(self) -> uuid.UUID:
        if self._call_id is None:
            raise RuntimeError("call has not started")
        return self._call_id
