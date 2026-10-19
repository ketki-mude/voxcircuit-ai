"""The booking workflow: runs only after the caller approves a ticket.

Two external writes, each recorded in booking_steps with its own idempotency key:
  1. crm_upsert      find the contact by email and update it, or create it
  2. calendar_event  create the event, or move it when rescheduling

Each step gets one retry. The booking succeeds only if both steps are done. Otherwise the
successful step stays recorded, the call is flagged, and the summary says exactly what
did and did not happen. Re-running the workflow for the same approval never duplicates
anything: finished steps are skipped and the external systems see the same keys.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.schemas import BookingOutcome, BookingTicket, StepName, StepReport
from app.integrations.base import CalendarClient, CrmClient, IntegrationError
from app.models import Appointment, Approval, AuditLog, BookingStep, Call, Customer, Lead, Slot

log = logging.getLogger(__name__)

STEP_ATTEMPTS = 2  # The first try plus one retry.


def idempotency_key(approval_id: uuid.UUID, step: StepName) -> str:
    return f"{approval_id}:{step}"


class BookingWorkflow:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        calendar: CalendarClient,
        crm: CrmClient,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._calendar = calendar
        self._crm = crm

    async def run(self, approval: Approval) -> BookingOutcome:
        ticket = BookingTicket.model_validate(approval.ticket)
        appointment_id = await self._prepare(approval, ticket)
        if appointment_id is None:
            return BookingOutcome(
                success=False,
                slot_label=ticket.slot_label,
                summary=f"Not booked: {ticket.slot_label} was just taken. "
                "Nothing was saved. Offer the caller other times.",
                steps=[],
            )
        crm = await self._run_step(
            approval.id, "crm_upsert", lambda key: self._crm_write(ticket, key)
        )
        calendar = await self._run_step(
            approval.id, "calendar_event", lambda key: self._calendar_write(ticket, key)
        )
        return await self._finish(approval, ticket, appointment_id, crm, calendar)

    async def _prepare(self, approval: Approval, ticket: BookingTicket) -> uuid.UUID | None:
        """Hold the slot, then save the lead and create the appointment and step rows."""
        lead = ticket.lead.model_dump()
        async with self._sessionmaker() as db, db.begin():
            # The slot's primary key is the double-booking guard: only a released slot, or
            # one this approval already holds, can be (re)claimed. Nothing else is written
            # if the slot is taken.
            held = await db.scalar(
                pg_insert(Slot)
                .values(
                    id=ticket.slot_id,
                    starts_at=ticket.starts_at,
                    ends_at=ticket.ends_at,
                    status="held",
                    approval_id=approval.id,
                )
                .on_conflict_do_update(
                    index_elements=[Slot.id],
                    set_={"status": "held", "approval_id": approval.id},
                    where=or_(Slot.status == "released", Slot.approval_id == approval.id),
                )
                .returning(Slot.id)
            )
            if held is None:
                db.add(_audit(approval, "booking.slot_taken", {"slot_id": ticket.slot_id}))
                return None
            await db.execute(
                pg_insert(Lead)
                .values(id=approval.lead_id, call_id=approval.call_id, **lead)
                .on_conflict_do_update(index_elements=[Lead.id], set_=lead)
            )
            await db.execute(
                pg_insert(Appointment)
                .values(
                    id=uuid.uuid4(),
                    call_id=approval.call_id,
                    approval_id=approval.id,
                    lead_id=approval.lead_id,
                    slot_id=ticket.slot_id,
                    starts_at=ticket.starts_at,
                    action=ticket.action,
                    replaces_appointment_id=ticket.replaces_appointment_id,
                )
                .on_conflict_do_nothing(index_elements=[Appointment.approval_id])
            )
            await db.execute(
                pg_insert(BookingStep)
                .values(
                    [
                        {
                            "approval_id": approval.id,
                            "step": step,
                            "idempotency_key": idempotency_key(approval.id, step),
                        }
                        for step in ("crm_upsert", "calendar_event")
                    ]
                )
                .on_conflict_do_nothing()
            )
            appointment_id = await db.scalar(
                select(Appointment.id).where(Appointment.approval_id == approval.id)
            )
            db.add(_audit(approval, "booking.started", {"appointment_id": str(appointment_id)}))
            return appointment_id

    async def _run_step(
        self,
        approval_id: uuid.UUID,
        step: StepName,
        write: Callable[[str], Awaitable[str]],
    ) -> StepReport:
        async with self._sessionmaker() as db:
            row = await db.scalar(
                select(BookingStep).where(
                    BookingStep.approval_id == approval_id, BookingStep.step == step
                )
            )
        if row is None:
            raise RuntimeError(f"booking step {step} missing for approval {approval_id}")
        if row.status == "done":
            return StepReport(step=step, status="done", attempts=row.attempts)

        attempts = row.attempts
        error = ""
        for _ in range(STEP_ATTEMPTS):
            attempts += 1
            try:
                external_id = await write(row.idempotency_key)
            except IntegrationError as exc:
                error = str(exc)
                log.warning("Booking step %s failed (attempt %d): %s", step, attempts, exc)
                await self._save_step(row.id, attempts=attempts, last_error=error)
                continue
            await self._save_step(row.id, attempts=attempts, status="done", external_id=external_id)
            return StepReport(step=step, status="done", attempts=attempts)
        await self._save_step(row.id, attempts=attempts, status="failed", last_error=error)
        return StepReport(step=step, status="failed", attempts=attempts)

    async def _save_step(self, step_id: int, **values: object) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(update(BookingStep).where(BookingStep.id == step_id).values(**values))

    async def _crm_write(self, ticket: BookingTicket, key: str) -> str:
        lead = ticket.lead
        properties = {
            "full_name": lead.full_name,
            "reason": lead.reason,
            "vehicle": f"{lead.vehicle_year} {lead.vehicle_make}",
            "zip_code": lead.zip_code,
            "source": "VoxCircuit demo",
        }
        if lead.current_insurer:
            properties["current_insurer"] = lead.current_insurer
        if lead.renewal_month:
            properties["renewal_month"] = lead.renewal_month
        contact = await self._crm.upsert_contact(
            email=lead.email, properties=properties, idempotency_key=key
        )
        return contact.id

    async def _calendar_write(self, ticket: BookingTicket, key: str) -> str:
        if ticket.action == "reschedule" and ticket.replaces_calendar_event_id:
            event = await self._calendar.move_event(
                ticket.replaces_calendar_event_id,
                starts_at=ticket.starts_at,
                ends_at=ticket.ends_at,
                idempotency_key=key,
            )
        else:
            # The event title carries the first name only; the CRM holds the details.
            event = await self._calendar.create_event(
                starts_at=ticket.starts_at,
                ends_at=ticket.ends_at,
                title=f"Northstar consultation: {ticket.lead.first_name}",
                idempotency_key=key,
            )
        return event.id

    async def _finish(
        self,
        approval: Approval,
        ticket: BookingTicket,
        appointment_id: uuid.UUID,
        crm: StepReport,
        calendar: StepReport,
    ) -> BookingOutcome:
        success = crm.status == "done" and calendar.status == "done"
        on_calendar = calendar.status == "done"
        async with self._sessionmaker() as db, db.begin():
            steps = {
                row.step: row
                for row in await db.scalars(
                    select(BookingStep).where(BookingStep.approval_id == approval.id)
                )
            }
            await db.execute(
                update(Appointment)
                .where(Appointment.id == appointment_id)
                .values(
                    status="booked" if success else "failed",
                    calendar_event_id=steps["calendar_event"].external_id,
                )
            )
            # The slot follows the calendar: if the event exists, the time is taken.
            await db.execute(
                update(Slot)
                .where(Slot.id == ticket.slot_id, Slot.approval_id == approval.id)
                .values(status="booked" if on_calendar else "released")
            )
            if crm.status == "done":
                # Once the CRM has their details, the caller is a customer who can verify
                # as a returning caller later.
                customer_id = await db.scalar(
                    pg_insert(Customer)
                    .values(
                        id=uuid.uuid4(), email=ticket.lead.email, full_name=ticket.lead.full_name
                    )
                    .on_conflict_do_update(
                        index_elements=[Customer.email],
                        set_={"full_name": ticket.lead.full_name},
                    )
                    .returning(Customer.id)
                )
                await db.execute(
                    update(Lead)
                    .where(Lead.id == approval.lead_id)
                    .values(crm_contact_id=steps["crm_upsert"].external_id, customer_id=customer_id)
                )
            if on_calendar and ticket.replaces_appointment_id is not None:
                await db.execute(
                    update(Appointment)
                    .where(Appointment.id == ticket.replaces_appointment_id)
                    .values(status="rescheduled")
                )
                if ticket.replaces_slot_id:
                    await db.execute(
                        update(Slot)
                        .where(Slot.id == ticket.replaces_slot_id)
                        .values(status="released")
                    )
            if not success:
                await db.execute(
                    update(Call).where(Call.id == approval.call_id).values(flagged=True)
                )
            db.add(
                _audit(
                    approval,
                    "booking.completed",
                    {
                        "success": success,
                        "appointment_id": str(appointment_id),
                        "steps": {crm.step: crm.status, calendar.step: calendar.status},
                    },
                )
            )
        return BookingOutcome(
            success=success,
            slot_label=ticket.slot_label,
            summary=_summary(ticket, crm, calendar),
            steps=[crm, calendar],
            appointment_id=appointment_id,
        )


def _summary(ticket: BookingTicket, crm: StepReport, calendar: StepReport) -> str:
    label = ticket.slot_label
    saved = crm.status == "done"
    on_calendar = calendar.status == "done"
    if saved and on_calendar:
        verb = "Rescheduled to" if ticket.action == "reschedule" else "Booked for"
        return f"{verb} {label}. The details are saved and the consultation is on the calendar."
    if saved:
        return (
            f"Not booked. The details were saved, but adding {label} to the calendar failed. "
            "A licensed agent will contact the caller to schedule."
        )
    if on_calendar:
        return (
            f"Not fully booked. {label} is held on the calendar, but saving the details "
            "failed. A licensed agent will contact the caller to confirm."
        )
    return (
        "Not booked. Neither the calendar nor the customer record could be updated. "
        "A licensed agent will contact the caller."
    )


def _audit(approval: Approval, event: str, payload: dict[str, object]) -> AuditLog:
    return AuditLog(
        call_id=approval.call_id,
        actor="system",
        event=event,
        payload={"approval_id": str(approval.id), **payload},
    )
