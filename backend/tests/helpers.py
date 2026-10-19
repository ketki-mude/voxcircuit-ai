"""Scripts and builders shared by the booking, verification and handoff tests."""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.schemas import BookingTicket, LeadDetails
from app.booking.slots import SlotTime, day_slots
from app.live.events import FunctionCall, LiveEvent, ToolCall
from app.live.fake import FakeLiveSession, ScriptStep, scripted_reply
from app.models import Appointment, Approval, Call, Customer, Lead

TZ = ZoneInfo("America/New_York")

LEAD = {
    "full_name": "Sam Testperson",
    "email": "sam@example.com",
    "reason": "switching_insurers",
    "vehicle_make": "Honda",
    "vehicle_year": 2019,
    "zip_code": "02139",
}

_ids = itertools.count(1)


def tool_call(name: str, **args: Any) -> ToolCall:
    return ToolCall(calls=(FunctionCall(id=f"fc-{next(_ids)}", name=name, args=args),))


def bookable_day(days_ahead: int = 3) -> date:
    """A weekday at least days_ahead days from now, in business time."""
    day = datetime.now(TZ).date() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


# Days used by tests, kept apart because held slots persist in the test database:
# booking flows over WebSocket use bookable_day(3), slot listing tests bookable_day(6),
# and fresh_slot() days from bookable_day(10) on.


def _unused_slots() -> "itertools.chain[SlotTime]":
    days = (bookable_day(10) + timedelta(days=offset) for offset in range(18))
    return itertools.chain.from_iterable(day_slots(day, TZ) for day in days)


_slots = _unused_slots()


def fresh_slot() -> SlotTime:
    """A future slot no other test has used, so held slots never collide across tests."""
    return next(_slots)


def request_booking_with_first_open_slot(action: str = "book") -> ScriptStep:
    def build(fake: FakeLiveSession) -> LiveEvent:
        slots = fake.last_response("check_availability")["slots"]
        lead_id = fake.last_response("create_or_update_lead")["lead_id"]
        return tool_call(
            "request_booking", slot_id=slots[0]["slot_id"], lead_id=lead_id, action=action
        )

    return build


def booking_script(day: date, lead: dict[str, Any] = LEAD) -> list[list[ScriptStep]]:
    """Greeting; then, after one caller turn, collect the lead, check the day, request the
    first open slot, and ask for approval; then announce whatever the system says."""
    return [
        scripted_reply("Hi, I'm Maya."),
        [tool_call("create_or_update_lead", **lead)],
        [tool_call("check_availability", date=day.isoformat())],
        [request_booking_with_first_open_slot()],
        scripted_reply("Please review the ticket on your screen."),
        scripted_reply("Thanks for waiting."),
    ]


def make_customer(
    db_engine: Engine, *, first_name: str, vehicle: tuple[int, str] = (2021, "Subaru")
) -> tuple[uuid.UUID, str]:
    """A returning customer with one earlier call and booked appointment.

    Returns the customer id and their (unique) email.
    """
    email = f"{first_name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    customer_id, call_id, approval_id, lead_id = (uuid.uuid4() for _ in range(4))
    starts_at = datetime(2026, 9, 1, 14, 0, tzinfo=TZ)
    with db_engine.begin() as conn:
        conn.execute(insert(Call).values(id=call_id, engine="fake", input_mode="text"))
        conn.execute(
            insert(Customer).values(id=customer_id, email=email, full_name=f"{first_name} Example")
        )
        conn.execute(
            insert(Approval).values(
                id=approval_id,
                call_id=call_id,
                action="book",
                status="approved",
                lead_id=lead_id,
                slot_id="2026-09-01T14:00",
                ticket={},
                expires_at=starts_at,
            )
        )
        conn.execute(
            insert(Lead).values(
                id=lead_id,
                call_id=call_id,
                customer_id=customer_id,
                full_name=f"{first_name} Example",
                email=email,
                reason="new_policy",
                vehicle_make=vehicle[1],
                vehicle_year=vehicle[0],
                zip_code="02139",
            )
        )
        conn.execute(
            insert(Appointment).values(
                id=uuid.uuid4(),
                call_id=call_id,
                approval_id=approval_id,
                lead_id=lead_id,
                slot_id="2026-09-01T14:00",
                starts_at=starts_at,
                action="book",
                status="booked",
            )
        )
    return customer_id, email


async def approval_for(
    sessions: async_sessionmaker[AsyncSession],
    slot: SlotTime,
    *,
    ticket_extra: dict[str, object] | None = None,
) -> Approval:
    """An approval row (as the caller approved it) for a new call."""
    ticket = BookingTicket(
        action="book",
        slot_id=slot.slot_id,
        slot_label=slot.label,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        lead=LeadDetails.model_validate(LEAD),
    ).model_copy(update=ticket_extra or {})
    async with sessions() as db, db.begin():
        call = Call(engine="fake", input_mode="text")
        db.add(call)
        await db.flush()
        approval = Approval(
            call_id=call.id,
            action=ticket.action,
            status="approved",
            lead_id=uuid.uuid4(),
            slot_id=slot.slot_id,
            ticket=ticket.model_dump(mode="json"),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(approval)
    return approval
