"""Interfaces to the external systems the booking workflow writes to.

INTEGRATIONS_MODE=mock uses MockCalendar and MockCrm (in-process, inspectable under
/mock/calendar and /mock/crm). Live clients (Google Calendar, HubSpot) come later and
implement the same protocols.
"""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel


class IntegrationError(Exception):
    """A call to an external system failed. The message is for logs, not for callers."""


class CalendarEvent(BaseModel):
    id: str
    starts_at: datetime
    ends_at: datetime
    title: str


class CrmContact(BaseModel):
    id: str
    email: str
    properties: dict[str, str]


class CalendarClient(Protocol):
    async def busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]: ...

    async def create_event(
        self, *, starts_at: datetime, ends_at: datetime, title: str, idempotency_key: str
    ) -> CalendarEvent: ...

    async def move_event(
        self, event_id: str, *, starts_at: datetime, ends_at: datetime, idempotency_key: str
    ) -> CalendarEvent: ...

    async def delete_event(self, event_id: str) -> bool: ...


class CrmClient(Protocol):
    async def upsert_contact(
        self, *, email: str, properties: dict[str, str], idempotency_key: str
    ) -> CrmContact:
        """Find the contact by email and update it, or create it."""
        ...

    async def delete_contact(self, *, email: str) -> bool: ...


FaultMode = Literal["ok", "fail_once", "fail_always", "fail_after_write_once"]


class Faults:
    """Failure injection for the mocks.

    fail_once and fail_always fail before writing. fail_after_write_once writes and then
    fails, like a timeout after the remote side committed; the idempotency key makes the
    retry return the existing record instead of a duplicate.
    """

    def __init__(self) -> None:
        self.mode: FaultMode = "ok"

    def before_write(self) -> None:
        if self.mode == "fail_always":
            raise IntegrationError("simulated outage")
        if self.mode == "fail_once":
            self.mode = "ok"
            raise IntegrationError("simulated failure")

    def after_write(self) -> None:
        if self.mode == "fail_after_write_once":
            self.mode = "ok"
            raise IntegrationError("simulated timeout after the write")
