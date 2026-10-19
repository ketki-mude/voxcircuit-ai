from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import CallerTokens
from app.booking.approvals import ApprovalService
from app.calls.recorder import CallRecorder
from app.calls.registry import CallRegistry
from app.integrations.base import CalendarClient
from app.safety.handoff import HandoffService
from app.verification.service import VerificationService


@dataclass(frozen=True)
class CallServices:
    """Everything a call handler needs besides its WebSocket and live session."""

    sessionmaker: async_sessionmaker[AsyncSession]
    recorder: CallRecorder
    calendar: CalendarClient
    approvals: ApprovalService
    registry: CallRegistry
    tokens: CallerTokens
    handoffs: HandoffService
    verification: VerificationService
