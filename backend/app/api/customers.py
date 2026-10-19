import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import caller_call_id
from app.customers import CustomerData, CustomerNotFound
from app.models import Call

router = APIRouter(prefix="/api/customers", tags=["customers"])


class DeletedResponse(BaseModel):
    deleted: bool
    calls_deleted: int
    crm_contact_deleted: bool
    calendar_events_deleted: int


@router.delete("/me")
async def delete_my_data(
    request: Request, call_id: Annotated[uuid.UUID, Depends(caller_call_id)]
) -> DeletedResponse:
    """Delete everything stored about the caller. "Me" is the customer this call verified
    as, so a caller can only delete their own data, and only after verifying."""
    async with request.app.state.sessionmaker() as db:
        customer_id = await db.scalar(select(Call.verified_customer_id).where(Call.id == call_id))
    if customer_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verify as a returning customer first")
    data: CustomerData = request.app.state.customer_data
    try:
        report = await data.delete(customer_id, current_call_id=call_id)
    except CustomerNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No data on file") from exc
    return DeletedResponse(
        deleted=True,
        calls_deleted=report.calls_deleted,
        crm_contact_deleted=report.crm_contact_deleted,
        calendar_events_deleted=report.calendar_events_deleted,
    )
