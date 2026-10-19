import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth import caller_call_id
from app.booking.approvals import ApprovalNotFound, ApprovalService
from app.booking.schemas import BookingOutcome

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecisionBody(BaseModel):
    decision: Literal["approve", "cancel"]


class ApprovalView(BaseModel):
    id: uuid.UUID
    status: str
    outcome: BookingOutcome | None


@router.post("/{approval_id}")
async def decide(
    approval_id: uuid.UUID,
    body: DecisionBody,
    request: Request,
    call_id: Annotated[uuid.UUID, Depends(caller_call_id)],
) -> ApprovalView:
    """Approve or cancel a booking ticket. Only the caller of that call may decide.

    Approving runs the booking workflow before responding. Deciding twice is safe: the
    second request returns the first decision.
    """
    service: ApprovalService = request.app.state.approvals
    try:
        approval = await service.decide(
            approval_id, call_id=call_id, approve=body.decision == "approve"
        )
    except ApprovalNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found") from exc
    return ApprovalView(
        id=approval.id,
        status=approval.status,
        outcome=BookingOutcome.model_validate(approval.outcome) if approval.outcome else None,
    )
