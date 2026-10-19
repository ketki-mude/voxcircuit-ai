import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calls.usage import UsageDelta
from app.models import AuditLog, Call, Message, UsageLog

Actor = Literal["caller", "model", "system", "team"]

# Serializes the daily-cap check across concurrent call starts.
_DAILY_CAP_LOCK_KEY = 7_301_001


def start_of_today() -> datetime:
    """Local midnight, timezone-aware. "Today" for the daily cap follows the server clock."""
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


class CallRecorder:
    """All database writes for a call. Every write also appends to audit_logs."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def start_call(
        self, *, engine: str, input_mode: str, model: str | None, daily_cap: int
    ) -> uuid.UUID | None:
        """Create the call row, or return None if the daily cap is already reached."""
        async with self._sessionmaker() as db, db.begin():
            await db.execute(select(func.pg_advisory_xact_lock(_DAILY_CAP_LOCK_KEY)))
            calls_today = await db.scalar(
                select(func.count()).select_from(Call).where(Call.started_at >= start_of_today())
            )
            if (calls_today or 0) >= daily_cap:
                db.add(
                    AuditLog(
                        actor="system",
                        event="call.rejected",
                        payload={
                            "reason": "daily_cap",
                            "calls_today": calls_today,
                            "cap": daily_cap,
                        },
                    )
                )
                return None
            call = Call(engine=engine, input_mode=input_mode, model=model)
            db.add(call)
            await db.flush()
            db.add(
                AuditLog(
                    call_id=call.id,
                    actor="caller",
                    event="call.started",
                    payload={"engine": engine, "input_mode": input_mode, "model": model},
                )
            )
            return call.id

    async def add_message(
        self,
        call_id: uuid.UUID,
        *,
        seq: int,
        role: Literal["user", "assistant"],
        source: Literal["typed", "transcription"],
        text: str,
        interrupted: bool = False,
        latency_ms: int | None = None,
    ) -> int:
        async with self._sessionmaker() as db, db.begin():
            message = Message(
                call_id=call_id,
                seq=seq,
                role=role,
                source=source,
                text=text,
                interrupted=interrupted,
                latency_ms=latency_ms,
            )
            db.add(message)
            await db.flush()
            db.add(
                AuditLog(
                    call_id=call_id,
                    actor="caller" if role == "user" else "model",
                    event=f"message.{role}",
                    payload={
                        "message_id": message.id,
                        "seq": seq,
                        "chars": len(text),
                        "interrupted": interrupted,
                        "latency_ms": latency_ms,
                    },
                )
            )
            return message.id

    async def add_usage(self, call_id: uuid.UUID, delta: UsageDelta) -> None:
        async with self._sessionmaker() as db, db.begin():
            db.add(
                UsageLog(
                    call_id=call_id,
                    audio_seconds_in=delta.audio_seconds_in,
                    audio_seconds_out=delta.audio_seconds_out,
                    prompt_tokens=delta.prompt_tokens,
                    response_tokens=delta.response_tokens,
                    total_tokens=delta.total_tokens,
                    estimated_cost_usd=delta.estimated_cost_usd,
                )
            )

    async def audit(
        self,
        call_id: uuid.UUID | None,
        actor: Actor,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessionmaker() as db, db.begin():
            db.add(AuditLog(call_id=call_id, actor=actor, event=event, payload=payload or {}))

    async def end_call(
        self, call_id: uuid.UUID, *, failed: bool, reason: str, summary: dict[str, Any]
    ) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                update(Call)
                .where(Call.id == call_id)
                .values(
                    ended_at=func.now(), status="failed" if failed else "ended", end_reason=reason
                )
            )
            db.add(
                AuditLog(
                    call_id=call_id,
                    actor="system",
                    event="call.ended",
                    payload={"reason": reason, **summary},
                )
            )
