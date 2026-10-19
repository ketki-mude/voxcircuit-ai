import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Reason = Literal["new_policy", "switching_insurers", "adding_vehicle"]
Action = Literal["book", "reschedule"]
StepName = Literal["crm_upsert", "calendar_event"]

REASON_LABELS: dict[str, str] = {
    "new_policy": "New policy",
    "switching_insurers": "Switching insurers",
    "adding_vehicle": "Adding a vehicle",
}


class LeadDetails(BaseModel):
    full_name: str
    email: str
    reason: Reason
    vehicle_make: str
    vehicle_year: int
    zip_code: str
    current_insurer: str | None = None
    renewal_month: str | None = None
    preferred_time: str | None = None

    @property
    def first_name(self) -> str:
        return self.full_name.split()[0]


class BookingTicket(BaseModel):
    """What the caller reviews and approves. The workflow books exactly this."""

    action: Action
    slot_id: str
    slot_label: str
    starts_at: datetime
    ends_at: datetime
    lead: LeadDetails
    replaces_appointment_id: uuid.UUID | None = None
    replaces_slot_id: str | None = None
    replaces_slot_label: str | None = None
    replaces_calendar_event_id: str | None = None

    def display_rows(self) -> list[tuple[str, str]]:
        lead = self.lead
        rows = [("Time", self.slot_label)]
        if self.replaces_slot_label:
            rows.append(("Replaces", self.replaces_slot_label))
        rows += [
            ("Name", lead.full_name),
            ("Email", lead.email),
            ("Reason", REASON_LABELS[lead.reason]),
            ("Vehicle", f"{lead.vehicle_year} {lead.vehicle_make}"),
            ("ZIP code", lead.zip_code),
        ]
        if lead.current_insurer:
            rows.append(("Current insurer", lead.current_insurer))
        if lead.renewal_month:
            rows.append(("Renewal month", lead.renewal_month))
        return rows


class StepReport(BaseModel):
    step: StepName
    status: Literal["done", "failed"]
    attempts: int


class BookingOutcome(BaseModel):
    success: bool
    slot_label: str
    summary: str  # What did and did not happen, in plain words.
    steps: list[StepReport]
    appointment_id: uuid.UUID | None = None
