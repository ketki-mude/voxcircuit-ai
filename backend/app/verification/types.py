import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CheckStatus = Literal["verified", "wrong_code", "failed", "expired", "no_challenge"]


@dataclass(frozen=True)
class Challenge:
    id: uuid.UUID
    masked_email: str
    code: str  # Demo only: shown in the caller's UI as a fake text message.
    expires_at: datetime


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    attempts_left: int
    returning_customer: bool = False
