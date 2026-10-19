import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import caller_call_id
from app.tools.specs import EMAIL_PATTERN
from app.verification.service import VerificationLimit, VerificationService
from app.verification.types import CheckStatus

router = APIRouter(prefix="/api/verify", tags=["verification"])

CallerCall = Annotated[uuid.UUID, Depends(caller_call_id)]


class StartBody(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)


class StartResponse(BaseModel):
    challenge_id: uuid.UUID
    masked_email: str
    expires_at: datetime


class CheckBody(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class CheckResponse(BaseModel):
    status: CheckStatus
    attempts_left: int


def _service(request: Request) -> VerificationService:
    service: VerificationService = request.app.state.verification
    return service


@router.post("/start")
async def start(body: StartBody, request: Request, call_id: CallerCall) -> StartResponse:
    """Send a code for the email on file. The demo code reaches the caller's screen over
    the call's WebSocket, never in this response."""
    try:
        challenge = await _service(request).start(call_id, body.email)
    except VerificationLimit as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "No more verification codes on this call"
        ) from exc
    return StartResponse(
        challenge_id=challenge.id,
        masked_email=challenge.masked_email,
        expires_at=challenge.expires_at,
    )


@router.post("/check")
async def check(body: CheckBody, request: Request, call_id: CallerCall) -> CheckResponse:
    result = await _service(request).check(call_id, body.code)
    return CheckResponse(status=result.status, attempts_left=result.attempts_left)
