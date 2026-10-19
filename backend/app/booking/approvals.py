"""Pending approvals: the only way the model can ask for a booking.

The model's request_booking tool creates a pending approval and nothing else. The caller
approves or cancels it in the UI (POST /api/approvals/{id}); only then does the backend
run the booking workflow. The result goes to the live call through the CallRegistry.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.schemas import BookingOutcome, BookingTicket
from app.booking.workflow import BookingWorkflow
from app.calls.registry import ApprovalResolved, CallRegistry
from app.models import Approval, AuditLog, Call

log = logging.getLogger(__name__)


class ApprovalNotFound(Exception):
    """No such approval for this call."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class ApprovalService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        workflow: BookingWorkflow,
        registry: CallRegistry,
        *,
        ttl_seconds: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._workflow = workflow
        self._registry = registry
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    async def create(
        self, *, call_id: uuid.UUID, lead_id: uuid.UUID, ticket: BookingTicket
    ) -> tuple[Approval, list[uuid.UUID]]:
        """Create a pending approval. Returns it and the ids of approvals it replaced."""
        now = self._clock()
        async with self._sessionmaker() as db, db.begin():
            # A new request replaces one the caller hasn't answered yet.
            replaced = list(
                await db.scalars(
                    update(Approval)
                    .where(Approval.call_id == call_id, Approval.status == "pending")
                    .values(status="cancelled", decided_by="system", decided_at=now)
                    .returning(Approval.id)
                )
            )
            approval = Approval(
                id=uuid.uuid4(),
                call_id=call_id,
                action=ticket.action,
                status="pending",
                lead_id=lead_id,
                slot_id=ticket.slot_id,
                ticket=ticket.model_dump(mode="json"),
                expires_at=now + self._ttl,
            )
            db.add(approval)
            for old_id in replaced:
                db.add(_audit(call_id, "approval.replaced", old_id, {"by": str(approval.id)}))
            db.add(
                _audit(
                    call_id,
                    "approval.created",
                    approval.id,
                    {"action": ticket.action, "slot_id": ticket.slot_id},
                )
            )
        return approval, replaced

    async def decide(
        self, approval_id: uuid.UUID, *, call_id: uuid.UUID, approve: bool
    ) -> Approval:
        """Record the caller's decision once; on approve, run the booking workflow.

        Deciding an approval that is no longer pending returns it unchanged, so a double
        click cannot book twice.
        """
        now = self._clock()
        async with self._sessionmaker() as db, db.begin():
            approval = await db.get(Approval, approval_id, with_for_update=True)
            if approval is None or approval.call_id != call_id:
                raise ApprovalNotFound
            if approval.status != "pending":
                return approval
            if approval.expires_at <= now:
                approval.status, approval.decided_by = "expired", "system"
            else:
                approval.status = "approved" if approve else "cancelled"
                approval.decided_by = "caller"
            approval.decided_at = now
            db.add(
                _audit(
                    call_id,
                    "approval.decided",
                    approval.id,
                    {"status": approval.status},
                    actor=approval.decided_by,
                )
            )

        outcome: BookingOutcome | None = None
        if approval.status == "approved":
            outcome = await self._run_workflow(approval)
            approval.outcome = outcome.model_dump(mode="json")
            async with self._sessionmaker() as db, db.begin():
                await db.execute(
                    update(Approval)
                    .where(Approval.id == approval.id)
                    .values(outcome=approval.outcome)
                )
        self._registry.deliver(call_id, ApprovalResolved(approval.id, approval.status, outcome))
        return approval

    async def expire_pending(self, call_id: uuid.UUID) -> list[uuid.UUID]:
        """Expire the call's unanswered approval, for example when the call ends."""
        async with self._sessionmaker() as db, db.begin():
            expired = list(
                await db.scalars(
                    update(Approval)
                    .where(Approval.call_id == call_id, Approval.status == "pending")
                    .values(status="expired", decided_by="system", decided_at=self._clock())
                    .returning(Approval.id)
                )
            )
            for approval_id in expired:
                db.add(_audit(call_id, "approval.decided", approval_id, {"status": "expired"}))
        return expired

    async def _run_workflow(self, approval: Approval) -> BookingOutcome:
        try:
            return await self._workflow.run(approval)
        except Exception:
            log.exception("Booking workflow crashed for approval %s", approval.id)
            async with self._sessionmaker() as db, db.begin():
                await db.execute(
                    update(Call).where(Call.id == approval.call_id).values(flagged=True)
                )
            return BookingOutcome(
                success=False,
                slot_label=str(approval.ticket.get("slot_label", "")),
                summary="Not booked. Something went wrong while booking. "
                "A licensed agent will contact the caller.",
                steps=[],
            )


def _audit(
    call_id: uuid.UUID,
    event: str,
    approval_id: uuid.UUID,
    payload: dict[str, object],
    *,
    actor: str = "system",
) -> AuditLog:
    return AuditLog(
        call_id=call_id,
        actor=actor,
        event=event,
        payload={"approval_id": str(approval_id), **payload},
    )
