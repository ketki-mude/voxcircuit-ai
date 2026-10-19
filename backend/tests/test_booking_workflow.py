"""The two-step booking workflow against the mock calendar and CRM."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.workflow import BookingWorkflow
from app.integrations.mock import MockCalendar, MockCrm
from app.models import Appointment, Approval, BookingStep, Call, Lead, Slot
from tests.helpers import approval_for, fresh_slot

Sessions = async_sessionmaker[AsyncSession]


async def steps_of(sessions: Sessions, approval: Approval) -> dict[str, BookingStep]:
    async with sessions() as db:
        rows = await db.scalars(select(BookingStep).where(BookingStep.approval_id == approval.id))
        return {row.step: row for row in rows}


async def row(sessions: Sessions, model: type, *where: object) -> object:
    async with sessions() as db:
        return await db.scalar(select(model).where(*where))  # type: ignore[arg-type]


def mocks() -> tuple[MockCalendar, MockCrm]:
    return MockCalendar(), MockCrm()


async def test_booking_writes_each_system_once(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    slot = fresh_slot()
    approval = await approval_for(sessionmaker, slot)

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert outcome.success
    assert outcome.summary.startswith(f"Booked for {slot.label}.")
    assert [(s.step, s.status, s.attempts) for s in outcome.steps] == [
        ("crm_upsert", "done", 1),
        ("calendar_event", "done", 1),
    ]
    assert len(calendar.events) == 1 and len(crm.contacts) == 1
    event = next(iter(calendar.events.values()))
    assert event.title == "Northstar consultation: Sam"  # First name only.
    steps = await steps_of(sessionmaker, approval)
    assert steps["calendar_event"].idempotency_key == f"{approval.id}:calendar_event"
    assert steps["calendar_event"].external_id == event.id
    appointment = await row(sessionmaker, Appointment, Appointment.approval_id == approval.id)
    assert isinstance(appointment, Appointment)
    assert (appointment.status, appointment.calendar_event_id) == ("booked", event.id)
    lead = await row(sessionmaker, Lead, Lead.id == approval.lead_id)
    assert isinstance(lead, Lead) and lead.crm_contact_id == crm.contacts["sam@example.com"].id
    slot_row = await row(sessionmaker, Slot, Slot.id == slot.slot_id)
    assert isinstance(slot_row, Slot) and slot_row.status == "booked"


async def test_one_retry_recovers_a_failed_step(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    crm.faults.mode = "fail_once"
    approval = await approval_for(sessionmaker, fresh_slot())

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert outcome.success
    assert [(s.step, s.attempts) for s in outcome.steps] == [
        ("crm_upsert", 2),
        ("calendar_event", 1),
    ]
    assert len(crm.contacts) == 1


async def test_retry_after_a_timeout_does_not_duplicate(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    calendar.faults.mode = "fail_after_write_once"  # The write lands, then the call fails.
    approval = await approval_for(sessionmaker, fresh_slot())

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert outcome.success
    assert len(calendar.events) == 1  # The retry reused the idempotency key.


async def test_crm_saved_but_calendar_failed(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    calendar.faults.mode = "fail_always"
    slot = fresh_slot()
    approval = await approval_for(sessionmaker, slot)

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert not outcome.success
    assert outcome.summary.startswith("Not booked. The details were saved")
    steps = await steps_of(sessionmaker, approval)
    assert (steps["crm_upsert"].status, steps["calendar_event"].status) == ("done", "failed")
    assert steps["calendar_event"].attempts == 2
    assert steps["calendar_event"].last_error == "simulated outage"
    assert calendar.events == {} and len(crm.contacts) == 1
    slot_row = await row(sessionmaker, Slot, Slot.id == slot.slot_id)
    assert isinstance(slot_row, Slot) and slot_row.status == "released"
    call = await row(sessionmaker, Call, Call.id == approval.call_id)
    assert isinstance(call, Call) and call.flagged


async def test_calendar_held_but_crm_failed(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    crm.faults.mode = "fail_always"
    slot = fresh_slot()
    approval = await approval_for(sessionmaker, slot)

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert not outcome.success
    assert "is held on the calendar, but saving the details failed" in outcome.summary
    assert len(calendar.events) == 1 and crm.contacts == {}
    # The event exists, so the slot stays taken; the appointment is not booked.
    slot_row = await row(sessionmaker, Slot, Slot.id == slot.slot_id)
    assert isinstance(slot_row, Slot) and slot_row.status == "booked"
    appointment = await row(sessionmaker, Appointment, Appointment.approval_id == approval.id)
    assert isinstance(appointment, Appointment) and appointment.status == "failed"
    call = await row(sessionmaker, Call, Call.id == approval.call_id)
    assert isinstance(call, Call) and call.flagged


async def test_both_systems_failed(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    calendar.faults.mode = crm.faults.mode = "fail_always"
    approval = await approval_for(sessionmaker, fresh_slot())

    outcome = await BookingWorkflow(sessionmaker, calendar, crm).run(approval)

    assert not outcome.success
    assert outcome.summary.startswith("Not booked. Neither the calendar nor the customer record")
    assert [s.status for s in outcome.steps] == ["failed", "failed"]


async def test_running_an_approval_again_changes_nothing(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    approval = await approval_for(sessionmaker, fresh_slot())
    workflow = BookingWorkflow(sessionmaker, calendar, crm)

    first = await workflow.run(approval)
    second = await workflow.run(approval)

    assert first.success and second.success
    assert first.appointment_id == second.appointment_id
    assert len(calendar.events) == 1 and len(crm.contacts) == 1
    assert {s.attempts for s in second.steps} == {1}
    async with sessionmaker() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.approval_id == approval.id)
        )
    assert count == 1


async def test_a_slot_cannot_be_booked_twice(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    slot = fresh_slot()
    first = await approval_for(sessionmaker, slot)
    second = await approval_for(sessionmaker, slot)
    workflow = BookingWorkflow(sessionmaker, calendar, crm)

    assert (await workflow.run(first)).success
    outcome = await workflow.run(second)

    assert not outcome.success
    assert outcome.summary == (
        f"Not booked: {slot.label} was just taken. Nothing was saved. Offer the caller other times."
    )
    assert len(calendar.events) == 1
    assert await row(sessionmaker, Lead, Lead.id == second.lead_id) is None
    assert await row(sessionmaker, Appointment, Appointment.approval_id == second.id) is None


async def test_reschedule_moves_the_event_and_frees_the_old_slot(sessionmaker: Sessions) -> None:
    calendar, crm = mocks()
    workflow = BookingWorkflow(sessionmaker, calendar, crm)
    old_slot, new_slot = fresh_slot(), fresh_slot()
    booked = await workflow.run(await approval_for(sessionmaker, old_slot))
    old = await row(sessionmaker, Appointment, Appointment.id == booked.appointment_id)
    assert isinstance(old, Appointment)

    moved = await workflow.run(
        await approval_for(
            sessionmaker,
            new_slot,
            ticket_extra={
                "action": "reschedule",
                "replaces_appointment_id": old.id,
                "replaces_slot_id": old.slot_id,
                "replaces_calendar_event_id": old.calendar_event_id,
            },
        )
    )

    assert moved.success
    assert moved.summary.startswith(f"Rescheduled to {new_slot.label}.")
    assert list(calendar.events) == [old.calendar_event_id]  # Moved, not duplicated.
    assert calendar.events[old.calendar_event_id or ""].starts_at == new_slot.starts_at
    old_after = await row(sessionmaker, Appointment, Appointment.id == old.id)
    assert isinstance(old_after, Appointment) and old_after.status == "rescheduled"
    old_slot_row = await row(sessionmaker, Slot, Slot.id == old_slot.slot_id)
    assert isinstance(old_slot_row, Slot) and old_slot_row.status == "released"
