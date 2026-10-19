"""Runs scenarios through the real call handler, tools, booking workflow and safety rules.

Each scenario gets its own mock calendar and CRM (with the scenario's fault modes), so an
evaluation never touches the app's integrations. Scripted mode plays Maya's turns with
FakeLiveSession; live mode uses the real model and counts against the daily cap.
"""

import asyncio
import itertools
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import CallerTokens
from app.booking.approvals import ApprovalService
from app.booking.schemas import BookingTicket
from app.booking.slots import SlotTime, day_slots
from app.booking.workflow import BookingWorkflow
from app.calls.handler import CallHandler
from app.calls.recorder import CallRecorder
from app.calls.registry import CallRegistry
from app.calls.services import CallServices
from app.config import Settings
from app.evaluation.checks import (
    Actual,
    count_history_leaks,
    count_unauthorized_writes,
    failures_for,
    read_actual,
)
from app.evaluation.loopback import LoopbackSocket, ScenarioError
from app.evaluation.schema import (
    CallerAction,
    CallerEntersCode,
    CallerSays,
    SayTurn,
    Scenario,
    ToolTurn,
)
from app.integrations.base import CalendarEvent
from app.integrations.mock import MockCalendar, MockCrm
from app.live.base import LiveSession
from app.live.events import FunctionCall, LiveEvent, ToolCall
from app.live.factory import EngineName, InputMode, LiveSessionFactory
from app.live.fake import FakeLiveSession, ScriptStep, scripted_reply
from app.models import Appointment, Approval, Call, Customer, Lead, Slot
from app.safety.handoff import HandoffService
from app.verification.service import VerificationService

log = logging.getLogger(__name__)

Mode = Literal["scripted", "live"]
_call_ids = itertools.count(1)


@dataclass
class ScenarioOutcome:
    scenario: Scenario
    passed: bool
    actual: Actual | None
    failures: list[str]
    call_id: uuid.UUID | None
    duration_ms: int


@dataclass
class _Context:
    calendar: MockCalendar
    crm: MockCrm
    workflow: BookingWorkflow
    services: CallServices
    variables: dict[str, str]
    forbidden: list[str]  # Values that must never reach the model or the caller.
    call_ids: list[uuid.UUID]


class ScenarioRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        live_factory: LiveSessionFactory | None = None,
        quiet_s: float = 0.15,
        live_quiet_s: float = 2.5,
        live_turn_timeout_s: float = 60,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._live_factory = live_factory
        self._quiet_s = quiet_s
        self._live_quiet_s = live_quiet_s
        self._live_turn_timeout_s = live_turn_timeout_s

    async def run(self, scenario: Scenario, mode: Mode) -> ScenarioOutcome:
        started = time.monotonic()
        ctx = await self._context(scenario)
        call_id: uuid.UUID | None = None
        try:
            call_id = await self._play(scenario, mode, ctx)
            actual = await read_actual(self._sessionmaker, call_id, ctx.calendar)
            actual.unauthorized_writes = await count_unauthorized_writes(
                self._sessionmaker, ctx.call_ids, ctx.calendar, ctx.crm
            )
            actual.history_leaks = await count_history_leaks(
                self._sessionmaker, call_id, ctx.forbidden
            )
            failures = failures_for(scenario.expect, actual)
        except (ScenarioError, TimeoutError) as exc:
            actual, failures = None, [str(exc) or "timed out"]
        finally:
            await self._release_slots(ctx.call_ids)
        return ScenarioOutcome(
            scenario=scenario,
            passed=not failures,
            actual=actual,
            failures=failures,
            call_id=call_id,
            duration_ms=round((time.monotonic() - started) * 1000),
        )

    async def _context(self, scenario: Scenario) -> _Context:
        calendar, crm = MockCalendar(), MockCrm()
        calendar.faults.mode = scenario.setup.calendar
        crm.faults.mode = scenario.setup.crm
        registry = CallRegistry()
        workflow = BookingWorkflow(self._sessionmaker, calendar, crm)
        services = CallServices(
            sessionmaker=self._sessionmaker,
            recorder=CallRecorder(self._sessionmaker),
            calendar=calendar,
            approvals=ApprovalService(
                self._sessionmaker,
                workflow,
                registry,
                ttl_seconds=self._settings.approval_ttl_seconds,
            ),
            registry=registry,
            tokens=CallerTokens(secrets.token_urlsafe(32)),
            handoffs=HandoffService(self._sessionmaker),
            verification=VerificationService(self._sessionmaker, registry),
        )
        tag = secrets.token_hex(4)
        day = self._bookable_day()
        variables = {"email": f"eval-{tag}@example.com", "day": day.isoformat()}
        forbidden: list[str] = []
        if scenario.setup.returning_customer:
            email = f"pat-{tag}@example.com"
            await self._seed_customer(email, "Pat Example", (2021, "Subaru"), day, calendar)
            variables["returning_email"] = email
            forbidden += [email, "Pat Example"]  # The summary carries the first name only.
        if scenario.setup.other_customer:
            email = f"lee-{tag}@example.com"
            await self._seed_customer(email, "Lee Example", (2015, "Volvo"), day, calendar)
            variables["other_email"] = email
            forbidden += [email, "Lee", "Volvo"]
        return _Context(calendar, crm, workflow, services, variables, forbidden, [])

    def _bookable_day(self) -> Any:
        day = datetime.now(self._settings.timezone).date() + timedelta(days=3)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    async def _seed_customer(
        self,
        email: str,
        full_name: str,
        vehicle: tuple[int, str],
        day: Any,
        calendar: MockCalendar,
    ) -> None:
        """A customer from an earlier call, with a booked appointment on the calendar."""
        slot = SlotTime(day_slots(day + timedelta(days=7), self._settings.timezone)[2].starts_at)
        event = CalendarEvent(
            id=f"evt_seed_{secrets.token_hex(4)}",
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            title=f"Northstar consultation: {full_name.split()[0]}",
        )
        calendar.events[event.id] = event
        customer_id, lead_id, approval_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with self._sessionmaker() as db, db.begin():
            call = Call(engine="fake", input_mode="text", purpose="evaluation", status="ended")
            db.add(call)
            db.add(Customer(id=customer_id, email=email, full_name=full_name))
            await db.flush()
            db.add(
                Approval(
                    id=approval_id,
                    call_id=call.id,
                    action="book",
                    status="approved",
                    lead_id=lead_id,
                    slot_id=slot.slot_id,
                    ticket={},
                    expires_at=datetime.now(UTC),
                )
            )
            db.add(
                Lead(
                    id=lead_id,
                    call_id=call.id,
                    customer_id=customer_id,
                    full_name=full_name,
                    email=email,
                    reason="new_policy",
                    vehicle_make=vehicle[1],
                    vehicle_year=vehicle[0],
                    zip_code="02139",
                )
            )
            await db.flush()
            db.add(
                Appointment(
                    call_id=call.id,
                    approval_id=approval_id,
                    lead_id=lead_id,
                    slot_id=slot.slot_id,
                    starts_at=slot.starts_at,
                    action="book",
                    status="booked",
                    calendar_event_id=event.id,
                )
            )

    async def _play(self, scenario: Scenario, mode: Mode, ctx: _Context) -> uuid.UUID:
        socket = LoopbackSocket()
        handler = CallHandler(
            websocket=socket,
            settings=self._settings,
            services=ctx.services,
            session_factory=self._session_factory(scenario, mode, ctx),
            purpose="evaluation",
        )
        task = asyncio.create_task(handler.run())
        try:
            await socket.send(
                {
                    "type": "start",
                    "engine": "fake" if mode == "scripted" else "gemini",
                    "input_mode": "text",
                    "consent": True,
                }
            )
            started = await socket.wait_for("call_started", "call_ended", "error")
            if started["type"] != "call_started":
                raise ScenarioError(f"The call did not start: {started.get('message')}")
            call_id = uuid.UUID(started["call_id"])
            ctx.call_ids.append(call_id)
            await self._settle(socket, mode, expect_reply=True)
            for step in scenario.caller:
                if socket.closed:
                    break
                await self._perform(step, socket, ctx, call_id, mode)
            if not socket.closed:
                await socket.send({"type": "end"})
            await socket.wait_for("call_ended")
            async with asyncio.timeout(10):
                await task
            return call_id
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait([task])

    def _session_factory(self, scenario: Scenario, mode: Mode, ctx: _Context) -> LiveSessionFactory:
        if mode == "live":
            if self._live_factory is None:
                raise ScenarioError("Live mode needs the Gemini session factory")
            factory = self._live_factory
            return lambda engine, input_mode: factory("gemini", "text")
        turns = [self._turn(turn, ctx.variables) for turn in scenario.model]

        def scripted(engine: EngineName, input_mode: InputMode) -> LiveSession:
            return FakeLiveSession(turns)

        return scripted

    def _turn(self, turn: SayTurn | ToolTurn, variables: dict[str, str]) -> list[ScriptStep]:
        if isinstance(turn, SayTurn):
            return scripted_reply(_fill(turn.say, variables))
        args = {key: _fill(value, variables) for key, value in turn.args.items()}
        name = turn.tool

        def build(fake: FakeLiveSession) -> LiveEvent:
            resolved = {key: _resolve(value, fake) for key, value in args.items()}
            call = FunctionCall(id=f"eval-{next(_call_ids)}", name=name, args=resolved)
            return ToolCall(calls=(call,))

        return [build]

    async def _perform(
        self,
        step: CallerSays | CallerAction | CallerEntersCode,
        socket: LoopbackSocket,
        ctx: _Context,
        call_id: uuid.UUID,
        mode: Mode,
    ) -> None:
        expect_reply = True
        match step:
            case CallerSays(say=text):
                await socket.send({"type": "user_text", "text": _fill(text, ctx.variables)})
            case CallerAction(action="approve" | "cancel" as action):
                await self._decide(socket, ctx, call_id, approve=action == "approve")
            case CallerAction(action="approve_if_ticket"):
                if not _open_ticket(socket):
                    return
                await self._decide(socket, ctx, call_id, approve=True)
            case CallerAction(action="competitor_takes_slot"):
                await self._competitor_takes_slot(socket, ctx)
                expect_reply = False
            case CallerAction(action="end"):
                await socket.send({"type": "end"})
                return
            case CallerEntersCode(enter_code=which):
                started = socket.latest("verification_started")
                if started is None:
                    raise ScenarioError("No verification code was sent")
                code = started["demo_code"]
                if which == "wrong":
                    code = f"{(int(code) + 1) % 1_000_000:06d}"
                await ctx.services.verification.check(call_id, code)
                expect_reply = False
        socket.mark_activity()
        await self._settle(socket, mode, expect_reply=expect_reply)

    async def _settle(self, socket: LoopbackSocket, mode: Mode, *, expect_reply: bool) -> None:
        if mode == "scripted":
            await socket.settle(self._quiet_s)
            return
        if expect_reply:
            # The real model takes a moment: wait for its reply, then for any follow-up.
            await socket.wait_for(
                "turn_complete", start=len(socket.frames), limit_s=self._live_turn_timeout_s
            )
        await socket.settle(self._live_quiet_s, limit_s=self._live_turn_timeout_s)

    async def _decide(
        self, socket: LoopbackSocket, ctx: _Context, call_id: uuid.UUID, *, approve: bool
    ) -> None:
        ticket = socket.latest("approval_requested")
        if ticket is None:
            raise ScenarioError("No booking ticket was shown")
        await ctx.services.approvals.decide(
            uuid.UUID(ticket["approval_id"]), call_id=call_id, approve=approve
        )

    async def _competitor_takes_slot(self, socket: LoopbackSocket, ctx: _Context) -> None:
        """Another caller books the ticket's slot first, through their own approval."""
        shown = socket.latest("approval_requested")
        if shown is None:
            raise ScenarioError("No booking ticket was shown")
        async with self._sessionmaker() as db:
            approval = await db.get(Approval, uuid.UUID(shown["approval_id"]))
        if approval is None:
            raise ScenarioError("The ticket's approval is missing")
        ticket = BookingTicket.model_validate(approval.ticket)
        rival_lead = ticket.lead.model_copy(
            update={"email": f"rival-{secrets.token_hex(4)}@example.com"}
        )
        async with self._sessionmaker() as db, db.begin():
            call = Call(engine="fake", input_mode="text", purpose="evaluation", status="ended")
            db.add(call)
            await db.flush()
            rival = Approval(
                call_id=call.id,
                action="book",
                status="approved",
                lead_id=uuid.uuid4(),
                slot_id=ticket.slot_id,
                ticket=ticket.model_copy(update={"lead": rival_lead}).model_dump(mode="json"),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            db.add(rival)
        ctx.call_ids.append(call.id)
        outcome = await ctx.workflow.run(rival)
        if not outcome.success:
            raise ScenarioError("The competing booking failed")

    async def _release_slots(self, call_ids: list[uuid.UUID]) -> None:
        """Free the slots this scenario held, so evaluations never fill the calendar."""
        if not call_ids:
            return
        async with self._sessionmaker() as db, db.begin():
            approvals = select(Approval.id).where(Approval.call_id.in_(call_ids))
            await db.execute(
                update(Slot).where(Slot.approval_id.in_(approvals)).values(status="released")
            )


def _fill(value: Any, variables: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    for name, replacement in variables.items():
        value = value.replace(f"{{{name}}}", replacement)
    return value


def _resolve(value: Any, fake: FakeLiveSession) -> Any:
    if value == "$first_slot":
        return fake.last_response("check_availability")["slots"][0]["slot_id"]
    if value == "$second_slot":
        return fake.last_response("check_availability")["slots"][1]["slot_id"]
    if value == "$lead_id":
        return next(
            r.response["lead_id"] for r in reversed(fake.tool_responses) if "lead_id" in r.response
        )
    return value


def _open_ticket(socket: LoopbackSocket) -> bool:
    ticket = socket.latest("approval_requested")
    if ticket is None:
        return False
    resolved = {f["approval_id"] for f in socket.frames if f["type"] == "approval_resolved"}
    return ticket["approval_id"] not in resolved
