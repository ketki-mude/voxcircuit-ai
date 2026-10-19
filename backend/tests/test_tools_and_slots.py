from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.slots import (
    MIN_NOTICE,
    SlotTime,
    booking_note,
    day_slots,
    open_slots,
    slot_from_id,
)
from app.integrations.base import IntegrationError
from app.integrations.mock import MockCalendar, MockCrm
from app.live.factory import function_specs
from app.live.gemini import function_declaration
from app.tools.specs import LeadArgs, RequestBookingArgs
from tests.helpers import LEAD, TZ, bookable_day


def test_every_tool_schema_converts_to_a_gemini_declaration() -> None:
    declarations = {
        spec.name: function_declaration(spec)  # Raises on unsupported schema fields.
        for spec in function_specs()
    }
    assert list(declarations) == [
        "check_availability",
        "create_or_update_lead",
        "request_booking",
        "start_verification",
        "get_previous_interactions",
        "handoff_to_human",
    ]
    without_args = [name for name, d in declarations.items() if d.parameters is None]
    assert without_args == ["get_previous_interactions"]


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"ssn": "123-45-6789"}, "Extra inputs are not permitted"),
        ({"full_name": "Sam 123-45-6789"}, "Social Security"),
        ({"vehicle_make": "Honda", "preferred_time": "VIN 1HGCM82633A004352"}, "VIN"),
        ({"current_insurer": "card 4111 1111 1111 1111"}, "payment card"),
        ({"full_name": "Sam born 04/12/1990"}, "date of birth"),
        ({"zip_code": "0213"}, "String should match pattern"),
        ({"email": "not-an-email"}, "String should match pattern"),
        ({"vehicle_year": datetime.now().year + 2}, "future"),
        ({"reason": "get a quote"}, "Input should be"),
    ],
)
def test_lead_args_reject_forbidden_or_invalid_data(args: dict[str, object], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        LeadArgs.model_validate(args)


def test_lead_args_accept_a_full_lead() -> None:
    assert LeadArgs.model_validate(LEAD | {"preferred_time": "2026-10-20 afternoon"}).zip_code
    with pytest.raises(ValidationError):
        RequestBookingArgs.model_validate({"slot_id": "tomorrow", "lead_id": "x"})


def test_day_slots_cover_business_hours_on_weekdays_only() -> None:
    day = bookable_day()
    slots = day_slots(day, TZ)
    assert len(slots) == 16
    assert (slots[0].slot_id[-5:], slots[-1].slot_id[-5:]) == ("09:00", "16:30")
    saturday = day + timedelta(days=(5 - day.weekday()) % 7)
    assert day_slots(saturday, TZ) == []


def test_slot_ids_must_be_offered_times() -> None:
    day = bookable_day().isoformat()
    slot = slot_from_id(f"{day}T13:30", TZ)
    assert slot is not None
    assert slot.label.endswith(("1:30 PM EDT", "1:30 PM EST"))
    for bad in (f"{day}T13:15", f"{day}T17:00", f"{day}T08:30", "2026-02-30T10:00", "soon"):
        assert slot_from_id(bad, TZ) is None


def test_booking_note_explains_unbookable_days() -> None:
    now = datetime.now(TZ)
    assert booking_note(now.date() - timedelta(days=1), now) == "That date is in the past."
    assert "30 days" in (booking_note(now.date() + timedelta(days=45), now) or "")
    assert booking_note(bookable_day(), now) is None


async def test_open_slots_skip_busy_held_and_too_soon(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    day = bookable_day(6)
    calendar = MockCalendar()
    first = day_slots(day, TZ)[0]
    await calendar.create_event(
        starts_at=first.starts_at, ends_at=first.ends_at, title="busy", idempotency_key="k1"
    )
    async with sessionmaker() as db:
        slots = await open_slots(db, calendar, day, now=datetime.now(UTC), tz=TZ)
        assert first not in slots
        assert len(slots) == 15
        # "Now" is just before the second slot: it and the first fall inside the notice.
        second = day_slots(day, TZ)[1]
        soon = await open_slots(db, calendar, day, now=second.starts_at - MIN_NOTICE / 2, tz=TZ)
        assert second not in soon and len(soon) == 14


async def test_mock_calendar_honors_idempotency_keys_and_faults() -> None:
    calendar = MockCalendar()
    slot = SlotTime(day_slots(bookable_day(), TZ)[2].starts_at)
    first = await calendar.create_event(
        starts_at=slot.starts_at, ends_at=slot.ends_at, title="t", idempotency_key="key-1"
    )
    again = await calendar.create_event(
        starts_at=slot.starts_at, ends_at=slot.ends_at, title="t", idempotency_key="key-1"
    )
    assert again == first and len(calendar.events) == 1

    calendar.faults.mode = "fail_once"
    with pytest.raises(IntegrationError):
        await calendar.create_event(
            starts_at=slot.starts_at, ends_at=slot.ends_at, title="t", idempotency_key="key-2"
        )
    mode_after: str = calendar.faults.mode  # fail_once resets itself after one failure.
    assert mode_after == "ok" and len(calendar.events) == 1

    calendar.faults.mode = "fail_after_write_once"
    with pytest.raises(IntegrationError):
        await calendar.create_event(
            starts_at=slot.starts_at, ends_at=slot.ends_at, title="t", idempotency_key="key-3"
        )
    retried = await calendar.create_event(
        starts_at=slot.starts_at, ends_at=slot.ends_at, title="t", idempotency_key="key-3"
    )
    assert len(calendar.events) == 2  # The retry returned the write, not a duplicate.
    assert retried.id in calendar.events


async def test_mock_crm_upserts_by_email() -> None:
    crm = MockCrm()
    created = await crm.upsert_contact(
        email="Sam@Example.com", properties={"a": "1"}, idempotency_key="k1"
    )
    updated = await crm.upsert_contact(
        email="sam@example.com", properties={"b": "2"}, idempotency_key="k2"
    )
    assert created.id == updated.id
    assert updated.properties == {"a": "1", "b": "2"}
    crm.faults.mode = "fail_always"
    for key in ("k3", "k4"):
        with pytest.raises(IntegrationError):
            await crm.upsert_contact(email="x@example.com", properties={}, idempotency_key=key)
    assert await crm.delete_contact(email="SAM@example.com")
    assert crm.contacts == {}
