"""Consultation slots: 30-minute times on weekdays from 9:00 to 17:00, business time.

A slot's id is its local start time, such as "2026-10-20T13:30", so the model can pass it
back unchanged. Slots only get a database row once a booking holds them.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.base import CalendarClient
from app.models import Slot

SLOT_MINUTES = 30
OPENS = time(9, 0)
CLOSES = time(17, 0)
MIN_NOTICE = timedelta(hours=1)
BOOKING_WINDOW_DAYS = 30
SLOT_ID_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"
_SLOT_ID_FORMAT = "%Y-%m-%dT%H:%M"


@dataclass(frozen=True)
class SlotTime:
    starts_at: datetime  # Timezone-aware, in business time.

    @property
    def slot_id(self) -> str:
        return self.starts_at.strftime(_SLOT_ID_FORMAT)

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=SLOT_MINUTES)

    @property
    def label(self) -> str:
        start = self.starts_at
        hour = start.strftime("%I").lstrip("0")
        return f"{start:%A, %B} {start.day} at {hour}:{start:%M %p} {start:%Z}"


def day_slots(day: date, tz: ZoneInfo) -> list[SlotTime]:
    if day.weekday() >= 5:
        return []
    slots = []
    start = datetime.combine(day, OPENS, tzinfo=tz)
    closes = datetime.combine(day, CLOSES, tzinfo=tz)
    while start + timedelta(minutes=SLOT_MINUTES) <= closes:
        slots.append(SlotTime(start))
        start += timedelta(minutes=SLOT_MINUTES)
    return slots


def slot_from_id(slot_id: str, tz: ZoneInfo) -> SlotTime | None:
    """The slot for an id, or None if the id is malformed or not an offered time."""
    if not re.fullmatch(SLOT_ID_PATTERN, slot_id):
        return None
    try:
        start = datetime.strptime(slot_id, _SLOT_ID_FORMAT).replace(tzinfo=tz)
    except ValueError:
        return None
    slot = SlotTime(start)
    return slot if slot in day_slots(start.date(), tz) else None


def booking_note(day: date, now: datetime) -> str | None:
    """Why a day can't be booked at all, or None if it can."""
    today = now.date()
    if day < today:
        return "That date is in the past."
    if day > today + timedelta(days=BOOKING_WINDOW_DAYS):
        return f"Only the next {BOOKING_WINDOW_DAYS} days can be booked."
    if day.weekday() >= 5:
        return "Consultations are offered Monday to Friday."
    return None


async def taken_slot_ids(db: AsyncSession, day: date, tz: ZoneInfo) -> set[str]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    rows = await db.scalars(
        select(Slot.id).where(
            Slot.status.in_(("held", "booked")),
            Slot.starts_at >= start,
            Slot.starts_at < start + timedelta(days=1),
        )
    )
    return set(rows)


async def open_slots(
    db: AsyncSession, calendar: CalendarClient, day: date, *, now: datetime, tz: ZoneInfo
) -> list[SlotTime]:
    """Offered slots on a day that are far enough ahead, not held, and free on the calendar."""
    if booking_note(day, now.astimezone(tz)) is not None:
        return []
    candidates = [s for s in day_slots(day, tz) if s.starts_at >= now + MIN_NOTICE]
    if not candidates:
        return []
    taken = await taken_slot_ids(db, day, tz)
    busy = await calendar.busy_intervals(candidates[0].starts_at, candidates[-1].ends_at)
    return [
        slot
        for slot in candidates
        if slot.slot_id not in taken
        and not any(start < slot.ends_at and end > slot.starts_at for start, end in busy)
    ]
