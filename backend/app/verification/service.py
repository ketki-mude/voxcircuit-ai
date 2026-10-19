"""Returning-customer verification. This is the only code that marks a call verified.

A challenge is a 6-digit code with 3 tries that expires after 5 minutes. In the demo the
code is shown in the caller's UI as a fake text message; in production it would go to the
phone on file. The model never sees the code and nothing it says can verify a caller.

The flow is the same whether or not the email belongs to a customer, so it does not
reveal who is a customer: an unknown email can be "verified" but unlocks no history.
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calls.registry import CallRegistry, VerificationChecked, VerificationStarted
from app.models import AuditLog, Call, Customer, VerificationAttempt
from app.verification.types import Challenge, CheckResult

CODE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 3
MAX_CHALLENGES_PER_CALL = 3


class VerificationLimit(Exception):
    """This call has used all its verification codes."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class VerificationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        registry: CallRegistry,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._registry = registry
        self._clock = clock

    async def start(self, call_id: uuid.UUID, email: str) -> Challenge:
        email = normalize_email(email)
        now = self._clock()
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_id = uuid.uuid4()
        async with self._sessionmaker() as db, db.begin():
            used = await db.scalar(
                select(func.count())
                .select_from(VerificationAttempt)
                .where(VerificationAttempt.call_id == call_id)
            )
            if (used or 0) >= MAX_CHALLENGES_PER_CALL:
                db.add(_audit(call_id, "verification.limit", {"challenges": used}))
                limited = True
            else:
                limited = False
                # A code that was never used stops working when a new one is sent.
                await db.execute(
                    update(VerificationAttempt)
                    .where(
                        VerificationAttempt.call_id == call_id,
                        VerificationAttempt.status == "pending",
                    )
                    .values(status="expired")
                )
                customer_id = await db.scalar(select(Customer.id).where(Customer.email == email))
                db.add(
                    VerificationAttempt(
                        id=challenge_id,
                        call_id=call_id,
                        customer_id=customer_id,
                        email_hash=_digest(email),
                        code_hash=_digest(str(challenge_id), code),
                        status="pending",
                        expires_at=now + CODE_TTL,
                    )
                )
                db.add(_audit(call_id, "verification.started", {"challenge_id": str(challenge_id)}))
        if limited:
            self._registry.deliver(call_id, VerificationChecked(CheckResult("failed", 0)))
            raise VerificationLimit
        challenge = Challenge(challenge_id, mask_email(email), code, now + CODE_TTL)
        self._registry.deliver(call_id, VerificationStarted(challenge))
        return challenge

    async def check(self, call_id: uuid.UUID, code: str) -> CheckResult:
        now = self._clock()
        async with self._sessionmaker() as db, db.begin():
            attempt = await db.scalar(
                select(VerificationAttempt)
                .where(
                    VerificationAttempt.call_id == call_id,
                    VerificationAttempt.status == "pending",
                )
                .order_by(VerificationAttempt.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            if attempt is None:
                return CheckResult("no_challenge", 0)
            if attempt.expires_at <= now:
                attempt.status = "expired"
                result = CheckResult("expired", 0)
            else:
                attempt.attempts += 1
                if hmac.compare_digest(attempt.code_hash, _digest(str(attempt.id), code.strip())):
                    attempt.status, attempt.verified_at = "verified", now
                    if attempt.customer_id is not None:
                        await db.execute(
                            update(Call)
                            .where(Call.id == call_id)
                            .values(verified_customer_id=attempt.customer_id, verified_at=now)
                        )
                    result = CheckResult(
                        "verified", 0, returning_customer=attempt.customer_id is not None
                    )
                elif attempt.attempts >= MAX_ATTEMPTS:
                    attempt.status = "failed"
                    result = CheckResult("failed", 0)
                else:
                    result = CheckResult("wrong_code", MAX_ATTEMPTS - attempt.attempts)
            db.add(
                _audit(
                    call_id,
                    "verification.attempt",
                    {
                        "challenge_id": str(attempt.id),
                        "result": result.status,
                        "attempts": attempt.attempts,
                    },
                )
            )
        self._registry.deliver(call_id, VerificationChecked(result))
        return result


def _audit(call_id: uuid.UUID, event: str, payload: dict[str, object]) -> AuditLog:
    return AuditLog(call_id=call_id, actor="caller", event=event, payload=payload)
