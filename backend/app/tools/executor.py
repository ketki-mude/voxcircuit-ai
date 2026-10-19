"""Runs the model's function calls for one call.

Read-only tools answer directly. Nothing here writes leads, appointments, the CRM or the
calendar: create_or_update_lead keeps an in-memory draft, and request_booking only creates
a pending approval. History is only returned for a caller the backend has verified.
Every call is validated, logged to tool_calls, and audited.
"""

import logging
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.approvals import ApprovalService
from app.booking.schemas import BookingTicket, LeadDetails
from app.booking.slots import SlotTime, booking_note, open_slots, slot_from_id
from app.calls.recorder import CallRecorder
from app.config import Settings
from app.customers import booked_appointment, latest_lead, previous_interactions
from app.integrations.base import CalendarClient, IntegrationError
from app.live.events import FunctionCall
from app.models import Appointment, Approval, Call
from app.safety.handoff import HandoffReason
from app.tools.specs import (
    TOOL_SPECS,
    CheckAvailabilityArgs,
    HandoffArgs,
    LeadArgs,
    NoArgs,
    RequestBookingArgs,
    StartVerificationArgs,
)
from app.verification.service import VerificationLimit, VerificationService

log = logging.getLogger(__name__)

REQUIRED_LEAD_FIELDS = ("full_name", "email", "reason", "vehicle_make", "vehicle_year", "zip_code")
FIELD_LABELS = {
    "full_name": "name",
    "email": "email",
    "reason": "reason",
    "vehicle_make": "vehicle make",
    "vehicle_year": "vehicle year",
    "zip_code": "ZIP code",
    "current_insurer": "current insurer",
    "renewal_month": "renewal month",
    "preferred_time": "preferred time",
}
MAX_SLOTS_OFFERED = 8
_SPECS = {spec.name: spec for spec in TOOL_SPECS}


class ToolError(Exception):
    """The tool could not do what was asked. The message is sent back to the model."""


@dataclass
class LeadDraft:
    """Lead details collected so far. Lives only in memory until a booking is approved."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    fields: dict[str, Any] = field(default_factory=dict)
    corrections: Counter[str] = field(default_factory=Counter)

    def apply(self, updates: dict[str, Any]) -> list[str]:
        """Merge updates; return the fields whose earlier value changed (corrections)."""
        corrected = []
        for name, value in updates.items():
            old = self.fields.get(name)
            if old is not None and _normalize(old) != _normalize(value):
                self.corrections[name] += 1
                corrected.append(name)
            self.fields[name] = value
        return corrected

    def missing(self) -> list[str]:
        return [name for name in REQUIRED_LEAD_FIELDS if name not in self.fields]


@dataclass
class ToolResult:
    response: dict[str, Any]  # Sent back to the model.
    ok: bool
    summary: str  # Shown in the caller's circuit timeline. No personal details.
    approval: Approval | None = None
    replaced_approvals: list[uuid.UUID] = field(default_factory=list)
    corrected: list[str] = field(default_factory=list)
    handoff: HandoffReason | None = None  # The model asked for a human.


class ToolExecutor:
    def __init__(
        self,
        *,
        call_id: uuid.UUID,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        calendar: CalendarClient,
        approvals: ApprovalService,
        verification: VerificationService,
        recorder: CallRecorder,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._call_id = call_id
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._calendar = calendar
        self._approvals = approvals
        self._verification = verification
        self._recorder = recorder
        self._clock = clock
        self.lead = LeadDraft()
        self.failures: Counter[str] = Counter()
        self._handlers: dict[str, Callable[[Any], Awaitable[ToolResult]]] = {
            "check_availability": self._check_availability,
            "create_or_update_lead": self._create_or_update_lead,
            "request_booking": self._request_booking,
            "start_verification": self._start_verification,
            "get_previous_interactions": self._get_previous_interactions,
            "handoff_to_human": self._handoff_to_human,
        }

    def forget_caller(self) -> None:
        """After "delete my data": drop the details collected so far."""
        self.lead = LeadDraft()

    async def execute(self, call: FunctionCall) -> ToolResult:
        started = time.monotonic()
        result = await self._dispatch(call)
        if not result.ok:
            self.failures[call.name] += 1
        await self._recorder.record_tool_call(
            self._call_id,
            provider_call_id=call.id,
            name=call.name,
            args=call.args,
            result=result.response,
            ok=result.ok,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return result

    async def _dispatch(self, call: FunctionCall) -> ToolResult:
        spec = _SPECS.get(call.name)
        handler = self._handlers.get(call.name)
        if spec is None or handler is None:
            return _error(f"There is no tool named {call.name}.")
        try:
            args = spec.args_model.model_validate(call.args)
        except ValidationError as exc:
            return _error(f"Invalid arguments: {_describe(exc)}")
        try:
            return await handler(args)
        except ToolError as exc:
            return _error(str(exc))
        except IntegrationError as exc:
            log.warning("Tool %s hit an integration error: %s", call.name, exc)
            return _error("The calendar did not respond. Try again in a moment.")

    async def _check_availability(self, args: CheckAvailabilityArgs) -> ToolResult:
        try:
            day = date.fromisoformat(args.date)
        except ValueError as exc:
            raise ToolError("date must be a real calendar date, as YYYY-MM-DD.") from exc
        tz = self._settings.timezone
        now = self._clock()
        async with self._sessionmaker() as db:
            slots = await open_slots(db, self._calendar, day, now=now, tz=tz)
        step = max(1, len(slots) // MAX_SLOTS_OFFERED)
        offered = slots[::step][:MAX_SLOTS_OFFERED]
        response: dict[str, Any] = {
            "date": args.date,
            "timezone": self._settings.business_timezone,
            "slots": [{"slot_id": s.slot_id, "label": s.label} for s in offered],
        }
        note = booking_note(day, now.astimezone(tz))
        if note or not offered:
            response["note"] = note or "No open times that day. Try another weekday."
        day_label = f"{day:%A, %B} {day.day}"
        summary = (
            f"{len(offered)} open times on {day_label}" if offered else f"No times on {day_label}"
        )
        return ToolResult(response, ok=True, summary=summary)

    async def _create_or_update_lead(self, args: LeadArgs) -> ToolResult:
        updates = args.model_dump(exclude_none=True)
        if not updates:
            raise ToolError("Pass at least one detail the caller gave.")
        if "email" in updates:
            updates["email"] = updates["email"].strip().lower()
        corrected = self.lead.apply(updates)
        response: dict[str, Any] = {
            "lead_id": str(self.lead.id),
            "saved": sorted(updates),
            "missing": self.lead.missing(),
            "note": "Draft only. Nothing is stored until the caller approves a booking.",
        }
        if corrected:
            response["corrected"] = corrected
        summary = "Noted " + ", ".join(FIELD_LABELS[name] for name in updates)
        return ToolResult(response, ok=True, summary=summary, corrected=corrected)

    async def _request_booking(self, args: RequestBookingArgs) -> ToolResult:
        if args.lead_id != str(self.lead.id):
            raise ToolError("Unknown lead_id. Use the lead_id from create_or_update_lead.")
        missing = self.lead.missing()
        if missing:
            labels = ", ".join(FIELD_LABELS[name] for name in missing)
            raise ToolError(f"Collect these details first: {labels}.")
        tz = self._settings.timezone
        slot = slot_from_id(args.slot_id, tz)
        if slot is None:
            raise ToolError("Unknown slot_id. Use a slot_id from check_availability.")
        async with self._sessionmaker() as db:
            available = await open_slots(
                db, self._calendar, slot.starts_at.date(), now=self._clock(), tz=tz
            )
            replaces = (
                await self._appointment_to_reschedule(db) if args.action == "reschedule" else None
            )
        if slot not in available:
            raise ToolError("That time is no longer available. Call check_availability again.")
        if args.action == "reschedule" and replaces is None:
            raise ToolError('There is no booked appointment to reschedule. Use action "book".')

        ticket = BookingTicket(
            action=args.action,
            slot_id=slot.slot_id,
            slot_label=slot.label,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            lead=LeadDetails.model_validate(self.lead.fields),
            replaces_appointment_id=replaces.id if replaces else None,
            replaces_slot_id=replaces.slot_id if replaces else None,
            replaces_slot_label=SlotTime(replaces.starts_at.astimezone(tz)).label
            if replaces
            else None,
            replaces_calendar_event_id=replaces.calendar_event_id if replaces else None,
        )
        approval, replaced = await self._approvals.create(
            call_id=self._call_id, lead_id=self.lead.id, ticket=ticket
        )
        response = {
            "status": "waiting_for_caller_approval",
            "approval_id": str(approval.id),
            "slot": slot.label,
            "instruction": "Ask the caller to review the booking ticket on their screen and "
            "press Approve or Cancel. It is not booked yet. The result will arrive as a "
            "[Northstar system] message.",
        }
        return ToolResult(
            response,
            ok=True,
            summary="Waiting for your OK",
            approval=approval,
            replaced_approvals=replaced,
        )

    async def _start_verification(self, args: StartVerificationArgs) -> ToolResult:
        try:
            await self._verification.start(self._call_id, args.email)
        except VerificationLimit as exc:
            raise ToolError(
                "This call has used all its verification codes. "
                "Offer to have a licensed agent follow up instead."
            ) from exc
        response = {
            "status": "code_sent",
            "instruction": "Ask the caller to type the 6-digit code from the text message on "
            "their screen. Never ask them to say it. A [Northstar system] message will tell "
            "you whether they are verified.",
        }
        return ToolResult(response, ok=True, summary="Sent a verification code")

    async def _get_previous_interactions(self, args: NoArgs) -> ToolResult:
        async with self._sessionmaker() as db:
            # Read from the database every time: only the verification service sets this.
            customer_id = await db.scalar(
                select(Call.verified_customer_id).where(Call.id == self._call_id)
            )
            if customer_id is None:
                raise ToolError(
                    "The caller is not verified. Ask for the email on file and call "
                    "start_verification. Never share history before that."
                )
            summary = await previous_interactions(db, customer_id, self._settings.timezone)
            lead = await latest_lead(db, customer_id)
        if lead is not None and not self.lead.fields:
            # Reuse the details on file, so a reschedule needs no re-collecting.
            self.lead.fields = {
                name: value for name in FIELD_LABELS if (value := getattr(lead, name)) is not None
            }
        response = {
            **summary,
            "lead_id": str(self.lead.id),
            "note": "Use the first name only. Never read out emails or full names. Their "
            "details on file are already in the lead; ask only about changes.",
        }
        return ToolResult(response, ok=True, summary="Loaded your previous visit")

    async def _handoff_to_human(self, args: HandoffArgs) -> ToolResult:
        reason: HandoffReason = "model_request" if args.reason == "other" else args.reason
        response = {
            "status": "handoff_requested",
            "instruction": "Tell the caller a licensed agent will follow up with them.",
        }
        return ToolResult(
            response, ok=True, summary="Asked a licensed agent to take over", handoff=reason
        )

    async def _appointment_to_reschedule(self, db: AsyncSession) -> Appointment | None:
        """The verified customer's booked appointment, or else the latest one booked in
        this call."""
        customer_id = await db.scalar(
            select(Call.verified_customer_id).where(Call.id == self._call_id)
        )
        if customer_id is not None:
            return await booked_appointment(db, customer_id)
        appointment: Appointment | None = await db.scalar(
            select(Appointment)
            .where(Appointment.call_id == self._call_id, Appointment.status == "booked")
            .order_by(Appointment.created_at.desc())
            .limit(1)
        )
        return appointment


def _error(message: str) -> ToolResult:
    return ToolResult({"error": message}, ok=False, summary="Could not complete that step")


def _describe(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        where = ".".join(str(part) for part in error["loc"])
        parts.append(f"{where}: {error['msg']}" if where else str(error["msg"]))
    return "; ".join(parts)


def _normalize(value: object) -> str:
    return " ".join(str(value).lower().split())
