"""Routes events from REST endpoints to the live call they belong to.

Calls live in this process (one uvicorn worker), so an in-memory map of inboxes is enough.
"""

import asyncio
import uuid
from dataclasses import dataclass

from app.booking.schemas import BookingOutcome
from app.verification.types import Challenge, CheckResult


@dataclass(frozen=True)
class ApprovalResolved:
    approval_id: uuid.UUID
    status: str  # approved, cancelled or expired
    outcome: BookingOutcome | None


@dataclass(frozen=True)
class VerificationStarted:
    challenge: Challenge


@dataclass(frozen=True)
class VerificationChecked:
    result: CheckResult


@dataclass(frozen=True)
class DataDeleted:
    """The caller's stored data was deleted at their request."""


@dataclass(frozen=True)
class HandoffTaken:
    """A team member took over the call from the control room."""

    agent_name: str


CallNotice = (
    ApprovalResolved | VerificationStarted | VerificationChecked | DataDeleted | HandoffTaken
)


class CallRegistry:
    def __init__(self) -> None:
        self._inboxes: dict[uuid.UUID, asyncio.Queue[CallNotice]] = {}

    def open(self, call_id: uuid.UUID) -> asyncio.Queue[CallNotice]:
        inbox: asyncio.Queue[CallNotice] = asyncio.Queue()
        self._inboxes[call_id] = inbox
        return inbox

    def close(self, call_id: uuid.UUID) -> None:
        self._inboxes.pop(call_id, None)

    def deliver(self, call_id: uuid.UUID, notice: CallNotice) -> bool:
        inbox = self._inboxes.get(call_id)
        if inbox is None:
            return False
        inbox.put_nowait(notice)
        return True
