import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Coroutine
from typing import Any, Literal, Protocol

from fastapi import WebSocketDisconnect
from pydantic import ValidationError
from starlette.types import Message as AsgiMessage
from starlette.websockets import WebSocketState

from app.booking.approvals import ApprovalService
from app.booking.schemas import BookingTicket
from app.calls import protocol as proto
from app.calls.latency import LatencyTracker
from app.calls.registry import (
    ApprovalResolved,
    CallNotice,
    DataDeleted,
    HandoffTaken,
    VerificationChecked,
    VerificationStarted,
)
from app.calls.services import CallServices
from app.calls.usage import UsageMeter
from app.config import Settings
from app.live import events as ev
from app.live.audio import INPUT_BYTES_PER_SECOND, is_speech
from app.live.base import (
    LiveConfigError,
    LiveQuotaError,
    LiveSession,
    LiveSessionError,
    ToolResponse,
)
from app.live.events import BYTES_PER_SAMPLE
from app.live.factory import InputMode, LiveSessionFactory
from app.models import Approval
from app.prompts import KICKOFF_TEXT, SYSTEM_PREFIX
from app.safety.handoff import REASON_LABELS, HandoffReason, HandoffRules
from app.safety.policy import check_reply, correction_notice
from app.tools.executor import ToolExecutor
from app.verification.types import CheckResult

log = logging.getLogger(__name__)

START_TIMEOUT_S = 30.0
# The caller token outlives the call a little, so a late click gets a clear answer.
TOKEN_GRACE_SECONDS = 300

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
    "handed_off": "A licensed agent has taken over and will contact you shortly.",
}
FAILED_REASONS: frozenset[proto.EndReason] = frozenset({"quota", "error"})


class CallSocket(Protocol):
    """What the handler needs from a WebSocket. Starlette's WebSocket fits, and so does the
    in-memory socket the evaluation runner uses to drive real calls."""

    application_state: WebSocketState

    async def accept(self) -> None: ...

    async def receive(self) -> AsgiMessage: ...

    async def send_text(self, data: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class CallHandler:
    """Runs one call on WS /ws/call: browser <-> backend <-> LiveSession.

    The browser sends a start message (with consent), then typed turns and, in an audio
    call, binary caller audio. The backend relays Maya's audio back, stores each finalized
    turn (typed text or transcript, never audio), runs the model's tools, relays booking
    approvals and verification, applies the handoff rules and the policy monitor, meters
    usage, measures latency and enforces the call caps.
    """

    def __init__(
        self,
        *,
        websocket: CallSocket,
        settings: Settings,
        services: CallServices,
        session_factory: LiveSessionFactory,
        purpose: Literal["live", "evaluation"] = "live",
    ) -> None:
        self._ws = websocket
        self._purpose = purpose
        self._settings = settings
        self._services = services
        self._recorder = services.recorder
        self._approvals: ApprovalService = services.approvals
        self._session_factory = session_factory
        self._inbox: asyncio.Queue[CallNotice] = asyncio.Queue()
        self._tools: ToolExecutor | None = None
        self._session: LiveSession | None = None  # Set while the live session is open.
        self._rules = HandoffRules()
        self._booking_confirmed = False
        self._end_after_reply = False  # Set when a team member takes over.
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
            purpose=self._purpose,
        )
        if call_id is None:
            await self._send(proto.CallEnded(reason="daily_cap", message=END_MESSAGES["daily_cap"]))
            await self._close()
            return
        self._call_id = call_id
        await self._recorder.audit(
            call_id, "caller", "call.consent", {"notice": proto.CONSENT_NOTICE}
        )
        self._inbox = self._services.registry.open(call_id)
        self._tools = ToolExecutor(
            call_id=call_id,
            settings=self._settings,
            sessionmaker=self._services.sessionmaker,
            calendar=self._services.calendar,
            approvals=self._approvals,
            verification=self._services.verification,
            recorder=self._recorder,
        )
        try:
            reason = await self._run_call(session, start)
            await self._finish(reason)
        finally:
            self._services.registry.close(call_id)

    async def _run_call(self, session: LiveSession, start: proto.StartCall) -> proto.EndReason:
        try:
            async with session:
                self._session = session
                self._started_at = time.monotonic()
                call_id = self._require_call_id()
                token = self._services.tokens.issue(
                    call_id, ttl_seconds=self._settings.max_call_seconds + TOKEN_GRACE_SECONDS
                )
                await self._send(
                    proto.CallStarted(
                        call_id=call_id,
                        engine=start.engine,
                        max_call_seconds=self._settings.max_call_seconds,
                        max_turns=self._settings.max_turns,
                        session_token=token,
                    )
                )
                await self._send_to_model(session, KICKOFF_TEXT)
                return await self._race(
                    self._pump_client(session),
                    self._pump_model(session),
                    self._pump_inbox(session),
                    self._call_timer(),
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
                    if SYSTEM_PREFIX[:10].lower() in text.lower():
                        await self._send(
                            proto.ErrorNotice(
                                code="invalid_message",
                                message="That looks like a system message, so it was not sent.",
                            )
                        )
                        continue
                    if self._user_turns >= self._settings.max_turns:
                        return "max_turns"
                    self._user_turns += 1
                    await self._save_message("user", "typed", text)
                    await self._send_to_model(session, text)
                    await self._check_caller(text)

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
                    if self._end_after_reply:
                        return "handed_off"
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
                case ev.ToolCall(calls=calls):
                    await self._run_tools(session, calls)
                case ev.ToolCallCancelled(ids=ids):
                    await self._recorder.audit(
                        self._call_id, "model", "tool.cancelled", {"provider_call_ids": list(ids)}
                    )
        log.warning("Call %s: live session ended without being closed", self._call_id)
        return "error"

    async def _run_tools(self, session: LiveSession, calls: tuple[ev.FunctionCall, ...]) -> None:
        tools = self._require_tools()
        responses = []
        handoffs: list[tuple[HandoffReason, bool]] = []
        for call in calls:
            result = await tools.execute(call)
            await self._send(proto.ToolEvent(name=call.name, ok=result.ok, summary=result.summary))
            for replaced in result.replaced_approvals:
                await self._send(
                    proto.ApprovalResolvedMessage(approval_id=replaced, status="cancelled")
                )
            if result.approval is not None:
                await self._send(_ticket(result.approval))
            responses.append(ToolResponse(id=call.id, name=call.name, response=result.response))
            if result.handoff is not None:
                handoffs.append((result.handoff, False))  # The tool response tells Maya.
            elif reason := HandoffRules.after_tool(
                call.name,
                ok=result.ok,
                failures=tools.failures,
                corrected=result.corrected,
                corrections=tools.lead.corrections,
            ):
                handoffs.append((reason, True))
        await session.send_tool_responses(responses)
        for reason, tell_model in handoffs:
            await self._hand_off(reason, tell_model=tell_model)

    async def _pump_inbox(self, session: LiveSession) -> proto.EndReason:
        """Events for this call from REST endpoints: approvals, verification, deletion."""
        while True:
            notice = await self._inbox.get()
            match notice:
                case ApprovalResolved():
                    await self._on_approval_resolved(notice)
                case VerificationStarted(challenge=challenge):
                    await self._send(
                        proto.VerificationStartedMessage(
                            challenge_id=challenge.id,
                            masked_email=challenge.masked_email,
                            demo_code=challenge.code,
                            expires_at=challenge.expires_at,
                        )
                    )
                case VerificationChecked(result=result):
                    await self._on_verification_checked(result)
                case HandoffTaken(agent_name=agent_name):
                    await self._send(proto.HandoffTakenMessage(agent_name=agent_name))
                    self._end_after_reply = True
                    await self._notify_model(
                        f"A licensed agent, {agent_name}, has taken over this call. Tell the "
                        f"caller {agent_name} will contact them shortly, thank them, and say "
                        "goodbye."
                    )
                case DataDeleted():
                    self._require_tools().forget_caller()
                    await self._send(proto.DataDeletedMessage())
                    await self._notify_model(
                        "The caller's stored data was deleted at their request. Do not use "
                        "or repeat any details they gave before this point."
                    )

    async def _on_verification_checked(self, result: CheckResult) -> None:
        await self._send(
            proto.VerificationResultMessage(
                status=result.status,
                attempts_left=result.attempts_left,
                returning_customer=result.returning_customer,
            )
        )
        if result.status == "verified":
            await self._notify_model(
                "The caller is verified as a returning customer. You may call "
                "get_previous_interactions now. Use their first name only."
                if result.returning_customer
                else "The caller entered the right code, but no previous interactions are "
                "on file for that email. Continue as a new caller."
            )
        elif result.status == "failed":
            await self._hand_off("verification_failed")
        elif result.status == "expired":
            await self._notify_model(
                "The verification code expired before the caller entered it. "
                "Offer to send a new one."
            )

    async def _on_approval_resolved(self, notice: ApprovalResolved) -> None:
        if notice.status not in ("approved", "cancelled", "expired"):
            return
        await self._send(
            proto.ApprovalResolvedMessage(approval_id=notice.approval_id, status=notice.status)
        )
        outcome = notice.outcome
        if outcome is not None:
            await self._send(
                proto.BookingResult(
                    approval_id=notice.approval_id,
                    success=outcome.success,
                    summary=outcome.summary,
                    steps=outcome.steps,
                )
            )
            close = (
                "Tell the caller it is confirmed."
                if outcome.success
                else "Tell the caller exactly this. It is not booked."
            )
            if outcome.success:
                self._booking_confirmed = True
            await self._notify_model(f"Booking result: {outcome.summary} {close}")
            if not outcome.success and outcome.steps:
                # A step failed after retrying; the summary already says an agent follows up.
                await self._hand_off("booking_incomplete", tell_model=False)
        elif notice.status == "cancelled":
            await self._notify_model(
                "The caller pressed Cancel on the booking ticket. Nothing was booked. "
                "Confirm that, and ask whether they want a different time."
            )
        elif notice.status == "expired":
            await self._notify_model(
                "The booking ticket expired before the caller approved it. Nothing was booked."
            )

    async def _check_caller(self, text: str) -> None:
        """Apply the handoff rules to a finalized caller turn."""
        reason = self._rules.caller_said(text)
        if reason is not None:
            await self._hand_off(reason)

    async def _hand_off(self, reason: HandoffReason, *, tell_model: bool = True) -> None:
        """Open a handoff (once per call), show it to the caller, and tell Maya."""
        if not await self._services.handoffs.open(self._require_call_id(), reason):
            return
        label = REASON_LABELS[reason]
        await self._send(proto.HandoffMessage(reason=reason, label=label))
        if tell_model:
            await self._notify_model(
                f"This call needs a licensed human agent ({label.lower()}). Tell the caller "
                "briefly that a licensed agent will follow up with them. Do not share any "
                "account history."
            )

    async def _notify_model(self, text: str) -> None:
        """Tell the model something only the backend knows. Maya then speaks about it."""
        session = self._session
        if session is None:
            return  # The call is over.
        self._latency.caller_input(time.monotonic())
        await session.send_text(f"{SYSTEM_PREFIX} {text}")
        await self._recorder.audit(self._call_id, "system", "system_notice.sent")

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
            await self._check_caller(text)

    async def _finish_reply(self, *, interrupted: bool) -> None:
        text = "".join(self._reply).strip()
        latency_ms = self._reply_latency_ms
        self._reply.clear()
        self._reply_open = False
        self._reply_latency_ms = None
        self._latency.reply_finished()
        if text:
            message_id = await self._save_message(
                "assistant",
                "transcription",
                text,
                interrupted=interrupted,
                latency_ms=latency_ms,
            )
            await self._monitor_reply(text, message_id)

    async def _monitor_reply(self, text: str, message_id: int) -> None:
        """The policy monitor. The words already played, so log, flag, and correct."""
        violations = check_reply(text, booking_confirmed=self._booking_confirmed)
        for violation in violations:
            await self._recorder.record_policy_event(
                self._require_call_id(), message_id=message_id, violation=violation
            )
        if violations:
            await self._notify_model(correction_notice(violations))

    async def _save_message(
        self,
        role: Literal["user", "assistant"],
        source: Literal["typed", "transcription"],
        text: str,
        *,
        interrupted: bool = False,
        latency_ms: int | None = None,
    ) -> int:
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
        return message_id

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
        self._session = None  # Closed: nothing more can be sent to the model.
        try:
            # Keep whatever was said when the call ended.
            await self._finish_caller_turn()
            await self._finish_reply(interrupted=True)
            delta = self._usage.take()
            if delta is not None:
                await self._recorder.add_usage(call_id, delta)
            # A ticket nobody answered can no longer be approved.
            for approval_id in await self._approvals.expire_pending(call_id):
                await self._send(
                    proto.ApprovalResolvedMessage(approval_id=approval_id, status="expired")
                )
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

    def _require_tools(self) -> ToolExecutor:
        if self._tools is None:
            raise RuntimeError("call has not started")
        return self._tools


def _ticket(approval: Approval) -> proto.ApprovalRequested:
    ticket = BookingTicket.model_validate(approval.ticket)
    return proto.ApprovalRequested(
        approval_id=approval.id,
        action=ticket.action,
        rows=[proto.TicketRow(label=label, value=value) for label, value in ticket.display_rows()],
        expires_at=approval.expires_at,
    )
