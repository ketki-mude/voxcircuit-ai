"""Returning customers: the minimal history Maya may see, and "delete my data"."""

import logging
import uuid
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calls.registry import CallRegistry, DataDeleted
from app.integrations.base import CalendarClient, CrmClient, IntegrationError
from app.models import (
    Appointment,
    Approval,
    AuditLog,
    Call,
    Customer,
    Lead,
    Message,
    PolicyEvent,
    ToolCall,
    VerificationAttempt,
)

log = logging.getLogger(__name__)


REDACTED = "[removed at the caller's request]"


class CustomerNotFound(Exception):
    pass


async def latest_lead(db: AsyncSession, customer_id: uuid.UUID) -> Lead | None:
    lead: Lead | None = await db.scalar(
        select(Lead).where(Lead.customer_id == customer_id).order_by(Lead.created_at.desc())
    )
    return lead


async def booked_appointment(db: AsyncSession, customer_id: uuid.UUID) -> Appointment | None:
    """The customer's latest appointment that is still booked."""
    appointment: Appointment | None = await db.scalar(
        select(Appointment)
        .join(Lead, Appointment.lead_id == Lead.id)
        .where(Lead.customer_id == customer_id, Appointment.status == "booked")
        .order_by(Appointment.created_at.desc())
        .limit(1)
    )
    return appointment


async def previous_interactions(
    db: AsyncSession, customer_id: uuid.UUID, tz: ZoneInfo
) -> dict[str, Any]:
    """First name, vehicle, and the last appointment's status and date. Nothing else:
    no email, no full name, no transcripts."""
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise CustomerNotFound
    lead = await latest_lead(db, customer_id)
    appointment: Appointment | None = await db.scalar(
        select(Appointment)
        .join(Lead, Appointment.lead_id == Lead.id)
        .where(Lead.customer_id == customer_id)
        .order_by(Appointment.created_at.desc())
        .limit(1)
    )
    last = None
    if appointment is not None:
        starts = appointment.starts_at.astimezone(tz)
        last = {
            "status": appointment.status,
            "date": f"{starts:%A, %B} {starts.day}, {starts.year}",
        }
    return {
        "first_name": customer.full_name.split()[0],
        "vehicle": f"{lead.vehicle_year} {lead.vehicle_make}" if lead else None,
        "last_appointment": last,
    }


@dataclass(frozen=True)
class DeletionReport:
    calls_deleted: int
    crm_contact_deleted: bool
    calendar_events_deleted: int


class CustomerData:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        calendar: CalendarClient,
        crm: CrmClient,
        registry: CallRegistry,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._calendar = calendar
        self._crm = crm
        self._registry = registry

    async def delete(self, customer_id: uuid.UUID, *, current_call_id: uuid.UUID) -> DeletionReport:
        """Delete everything stored about a customer: their calls, transcripts, leads,
        appointments, approvals and verification records, the CRM contact and their
        calendar events. The current call goes on, minus what it stored so far; its policy
        events stay with the excerpt removed. Audit logs stay; they hold ids only."""
        async with self._sessionmaker() as db, db.begin():
            customer = await db.get(Customer, customer_id)
            if customer is None:
                raise CustomerNotFound
            email = customer.email
            leads = list(await db.scalars(select(Lead).where(Lead.customer_id == customer_id)))
            event_ids = [
                event_id
                for event_id in await db.scalars(
                    select(Appointment.calendar_event_id).where(
                        Appointment.lead_id.in_([lead.id for lead in leads]),
                        Appointment.status == "booked",
                    )
                )
                if event_id
            ]
            linked_calls = (
                {lead.call_id for lead in leads}
                | set(
                    await db.scalars(
                        select(VerificationAttempt.call_id).where(
                            VerificationAttempt.customer_id == customer_id
                        )
                    )
                )
                | set(
                    await db.scalars(
                        select(Call.id).where(Call.verified_customer_id == customer_id)
                    )
                )
            )
            other_calls = linked_calls - {current_call_id}
            for model in (Message, ToolCall, Approval, Lead, VerificationAttempt):
                await db.execute(delete(model).where(model.call_id == current_call_id))
            # Keep the record that Maya broke a rule, but not her words, which may quote
            # the caller.
            await db.execute(
                update(PolicyEvent)
                .where(PolicyEvent.call_id == current_call_id)
                .values(excerpt=REDACTED)
            )
            await db.execute(
                update(Call)
                .where(Call.id == current_call_id)
                .values(verified_customer_id=None, verified_at=None)
            )
            if other_calls:
                await db.execute(delete(Call).where(Call.id.in_(other_calls)))
            await db.execute(delete(Customer).where(Customer.id == customer_id))
            db.add(
                AuditLog(
                    call_id=current_call_id,
                    actor="caller",
                    event="customer.deleted",
                    payload={"calls_deleted": len(other_calls), "events": len(event_ids)},
                )
            )

        # External systems, after the database commit.
        try:
            crm_deleted = await self._crm.delete_contact(email=email)
        except IntegrationError:
            log.exception("Could not delete the CRM contact")
            crm_deleted = False
        events_deleted = 0
        for event_id in event_ids:
            try:
                await self._calendar.delete_event(event_id)
                events_deleted += 1
            except IntegrationError:
                log.exception("Could not delete calendar event %s", event_id)
        self._registry.deliver(current_call_id, DataDeleted())
        return DeletionReport(len(other_calls), crm_deleted, events_deleted)
