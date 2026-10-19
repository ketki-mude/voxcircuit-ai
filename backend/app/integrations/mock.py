import asyncio
import uuid
from datetime import datetime

from app.integrations.base import CalendarEvent, CrmContact, Faults, IntegrationError


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class MockCalendar:
    """In-memory calendar that honors idempotency keys. State is lost on restart."""

    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self.faults = Faults()
        self.write_keys: list[str] = []  # Idempotency key of every write, for audits.
        self._by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        return [
            (event.starts_at, event.ends_at)
            for event in self.events.values()
            if event.starts_at < end and event.ends_at > start
        ]

    async def create_event(
        self, *, starts_at: datetime, ends_at: datetime, title: str, idempotency_key: str
    ) -> CalendarEvent:
        async with self._lock:
            if idempotency_key in self._by_key:
                return self.events[self._by_key[idempotency_key]]
            self.faults.before_write()
            event = CalendarEvent(
                id=_new_id("evt"), starts_at=starts_at, ends_at=ends_at, title=title
            )
            self.events[event.id] = event
            self._by_key[idempotency_key] = event.id
            self.write_keys.append(idempotency_key)
            self.faults.after_write()
            return event

    async def move_event(
        self, event_id: str, *, starts_at: datetime, ends_at: datetime, idempotency_key: str
    ) -> CalendarEvent:
        async with self._lock:
            if idempotency_key in self._by_key:
                return self.events[self._by_key[idempotency_key]]
            self.faults.before_write()
            existing = self.events.get(event_id)
            if existing is None:
                raise IntegrationError(f"calendar event {event_id} not found")
            moved = existing.model_copy(update={"starts_at": starts_at, "ends_at": ends_at})
            self.events[event_id] = moved
            self._by_key[idempotency_key] = event_id
            self.write_keys.append(idempotency_key)
            self.faults.after_write()
            return moved

    async def delete_event(self, event_id: str) -> bool:
        async with self._lock:
            return self.events.pop(event_id, None) is not None


class MockCrm:
    """In-memory CRM keyed by email that honors idempotency keys. State is lost on restart."""

    def __init__(self) -> None:
        self.contacts: dict[str, CrmContact] = {}  # By lowercase email.
        self.faults = Faults()
        self.write_keys: list[str] = []  # Idempotency key of every write, for audits.
        self._by_key: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def upsert_contact(
        self, *, email: str, properties: dict[str, str], idempotency_key: str
    ) -> CrmContact:
        key = email.strip().lower()
        async with self._lock:
            if idempotency_key in self._by_key:
                return self.contacts[self._by_key[idempotency_key]]
            self.faults.before_write()
            existing = self.contacts.get(key)
            contact = CrmContact(
                id=existing.id if existing else _new_id("crm"),
                email=key,
                properties={**(existing.properties if existing else {}), **properties},
            )
            self.contacts[key] = contact
            self._by_key[idempotency_key] = key
            self.write_keys.append(idempotency_key)
            self.faults.after_write()
            return contact

    async def delete_contact(self, *, email: str) -> bool:
        async with self._lock:
            return self.contacts.pop(email.strip().lower(), None) is not None
