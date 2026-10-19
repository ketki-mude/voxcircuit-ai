"""Team control room: metrics, calls, and the handoff queue."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app import reporting
from app.auth import Agent, Viewer
from app.booking.schemas import BookingTicket
from app.calls.recorder import start_of_today
from app.calls.registry import CallRegistry, HandoffTaken
from app.config import Settings
from app.models import (
    Approval,
    AuditLog,
    BookingStep,
    Call,
    Handoff,
    Message,
    PolicyEvent,
    TeamUser,
    ToolCall,
    UsageLog,
    VerificationAttempt,
)
from app.safety.handoff import reason_label

router = APIRouter(prefix="/api", tags=["control room"])


@router.get("/control-room/summary")
async def control_room_summary(request: Request, _: Viewer) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    async with request.app.state.sessionmaker() as db:
        data = await reporting.summary(db, since=start_of_today())
    data["usage"]["daily_call_cap"] = settings.daily_call_cap
    data["settings"] = {
        "model": settings.gemini_live_model,
        "integrations_mode": settings.integrations_mode,
        "max_call_seconds": settings.max_call_seconds,
        "max_turns": settings.max_turns,
        "daily_call_cap": settings.daily_call_cap,
        "vad_silence_duration_ms": settings.vad_silence_duration_ms,
        "approval_ttl_seconds": settings.approval_ttl_seconds,
        "business_timezone": settings.business_timezone,
    }
    return data


class CallRow(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    engine: str
    input_mode: str
    purpose: str
    status: str
    end_reason: str | None
    flagged: bool
    user_turns: int
    booked: bool
    handoff_reason: str | None
    policy_flags: int
    estimated_cost_usd: float


@router.get("/calls")
async def list_calls(
    request: Request,
    _: Viewer,
    purpose: Literal["live", "evaluation", "all"] = "live",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[CallRow]:
    async with request.app.state.sessionmaker() as db:
        rows = (
            await db.execute(reporting.call_list_query(purpose).limit(limit).offset(offset))
        ).all()
    return [
        CallRow(
            id=call.id,
            started_at=call.started_at,
            ended_at=call.ended_at,
            duration_seconds=round((call.ended_at - call.started_at).total_seconds())
            if call.ended_at
            else None,
            engine=call.engine,
            input_mode=call.input_mode,
            purpose=call.purpose,
            status=call.status,
            end_reason=call.end_reason,
            flagged=call.flagged,
            user_turns=user_turns,
            booked=booked > 0,
            handoff_reason=handoff_reason,
            policy_flags=policy_flags,
            estimated_cost_usd=float(cost),
        )
        for call, user_turns, booked, handoff_reason, policy_flags, cost in rows
    ]


@router.get("/calls/{call_id}")
async def call_detail(call_id: uuid.UUID, request: Request, _: Agent) -> dict[str, Any]:
    """Everything recorded about one call. Agents only: transcripts hold personal details."""
    async with request.app.state.sessionmaker() as db:
        call = await db.get(Call, call_id)
        if call is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
        messages = await db.scalars(
            select(Message).where(Message.call_id == call_id).order_by(Message.seq)
        )
        tools = await db.scalars(
            select(ToolCall).where(ToolCall.call_id == call_id).order_by(ToolCall.id)
        )
        approvals = list(
            await db.scalars(
                select(Approval).where(Approval.call_id == call_id).order_by(Approval.created_at)
            )
        )
        steps = await db.scalars(
            select(BookingStep)
            .where(BookingStep.approval_id.in_([a.id for a in approvals]))
            .order_by(BookingStep.id)
        )
        handoffs = (
            await db.execute(
                select(Handoff, TeamUser.name)
                .outerjoin(TeamUser, TeamUser.id == Handoff.taken_by)
                .where(Handoff.call_id == call_id)
            )
        ).all()
        policy = await db.scalars(
            select(PolicyEvent).where(PolicyEvent.call_id == call_id).order_by(PolicyEvent.id)
        )
        verification = await db.scalars(
            select(VerificationAttempt)
            .where(VerificationAttempt.call_id == call_id)
            .order_by(VerificationAttempt.created_at)
        )
        usage = (
            await db.execute(
                select(
                    func.coalesce(func.sum(UsageLog.audio_seconds_in), 0),
                    func.coalesce(func.sum(UsageLog.audio_seconds_out), 0),
                    func.coalesce(func.sum(UsageLog.estimated_cost_usd), 0),
                ).where(UsageLog.call_id == call_id)
            )
        ).one()
        p50, p95, samples = await reporting.latency_percentiles(db, Call.id == call_id)
        audit = await db.scalars(
            select(AuditLog).where(AuditLog.call_id == call_id).order_by(AuditLog.id).limit(500)
        )
        steps_by_approval: dict[uuid.UUID, list[dict[str, Any]]] = {}
        for step in steps:
            steps_by_approval.setdefault(step.approval_id, []).append(
                {
                    "step": step.step,
                    "status": step.status,
                    "attempts": step.attempts,
                    "idempotency_key": step.idempotency_key,
                    "last_error": step.last_error,
                }
            )
        return {
            "call": {
                "id": call.id,
                "started_at": call.started_at,
                "ended_at": call.ended_at,
                "engine": call.engine,
                "input_mode": call.input_mode,
                "model": call.model,
                "purpose": call.purpose,
                "status": call.status,
                "end_reason": call.end_reason,
                "flagged": call.flagged,
                "verified": call.verified_customer_id is not None,
            },
            "messages": [
                {
                    "seq": m.seq,
                    "role": m.role,
                    "source": m.source,
                    "text": m.text,
                    "interrupted": m.interrupted,
                    "latency_ms": m.latency_ms,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
            "tool_calls": [
                {
                    "name": t.name,
                    "status": t.status,
                    "duration_ms": t.duration_ms,
                    "args": t.args,
                    "result": t.result,
                    "created_at": t.created_at,
                }
                for t in tools
            ],
            "approvals": [
                {
                    "id": a.id,
                    "action": a.action,
                    "status": a.status,
                    "slot": BookingTicket.model_validate(a.ticket).slot_label
                    if a.ticket
                    else a.slot_id,
                    "decided_by": a.decided_by,
                    "created_at": a.created_at,
                    "decided_at": a.decided_at,
                    "outcome": a.outcome,
                    "steps": steps_by_approval.get(a.id, []),
                }
                for a in approvals
            ],
            "handoffs": [
                {
                    "id": h.id,
                    "reason": h.reason,
                    "label": reason_label(h.reason),
                    "status": h.status,
                    "created_at": h.created_at,
                    "taken_at": h.taken_at,
                    "taken_by": taken_by,
                }
                for h, taken_by in handoffs
            ],
            "policy_events": [
                {"rule": e.rule, "excerpt": e.excerpt, "created_at": e.created_at} for e in policy
            ],
            "verification": [
                {"status": v.status, "attempts": v.attempts, "created_at": v.created_at}
                for v in verification
            ],
            "usage": {
                "audio_seconds_in": float(usage[0]),
                "audio_seconds_out": float(usage[1]),
                "estimated_cost_usd": float(usage[2]),
                "latency_p50_ms": p50,
                "latency_p95_ms": p95,
                "latency_samples": samples,
            },
            "audit": [
                {
                    "event": a.event,
                    "actor": a.actor,
                    "payload": a.payload,
                    "created_at": a.created_at,
                }
                for a in audit
            ],
        }


class HandoffRow(BaseModel):
    id: uuid.UUID
    call_id: uuid.UUID
    reason: str
    label: str
    status: str
    created_at: datetime
    taken_at: datetime | None
    taken_by: str | None
    call_active: bool
    call_started_at: datetime


_HANDOFF_ROWS = (
    select(Handoff, TeamUser.name, Call.status, Call.started_at)
    .join(Call, Call.id == Handoff.call_id)
    .outerjoin(TeamUser, TeamUser.id == Handoff.taken_by)
    .where(Call.purpose == "live")
    .order_by(Handoff.created_at)
)


def _handoff_row(
    h: Handoff, taken_by: str | None, call_status: str, started_at: datetime
) -> HandoffRow:
    return HandoffRow(
        id=h.id,
        call_id=h.call_id,
        reason=h.reason,
        label=reason_label(h.reason),
        status=h.status,
        created_at=h.created_at,
        taken_at=h.taken_at,
        taken_by=taken_by,
        call_active=call_status == "active",
        call_started_at=started_at,
    )


@router.get("/handoffs")
async def list_handoffs(
    request: Request,
    _: Viewer,
    status_filter: Literal["open", "taken", "all"] = Query(default="open", alias="status"),
) -> list[HandoffRow]:
    """The "Needs a human" queue, oldest first. Live calls only."""
    query = _HANDOFF_ROWS
    if status_filter != "all":
        query = query.where(Handoff.status == status_filter)
    async with request.app.state.sessionmaker() as db:
        rows = (await db.execute(query.limit(200))).all()
    return [_handoff_row(*row) for row in rows]


@router.post("/handoffs/{handoff_id}/take-over")
async def take_over(handoff_id: uuid.UUID, request: Request, user: Agent) -> HandoffRow:
    """Take a call from the queue. If the call is still live, Maya hands it over and ends."""
    async with request.app.state.sessionmaker() as db, db.begin():
        taken = await db.scalar(
            update(Handoff)
            .where(Handoff.id == handoff_id, Handoff.status == "open")
            .values(status="taken", taken_at=func.now(), taken_by=user.id)
            .returning(Handoff.call_id)
        )
        if taken is None:
            exists = await db.scalar(select(Handoff.status).where(Handoff.id == handoff_id))
            if exists is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
            raise HTTPException(status.HTTP_409_CONFLICT, "Someone already took this call")
        db.add(
            AuditLog(
                call_id=taken,
                actor="team",
                event="handoff.taken",
                payload={"handoff_id": str(handoff_id), "user_id": str(user.id)},
            )
        )
    registry: CallRegistry = request.app.state.registry
    registry.deliver(taken, HandoffTaken(agent_name=user.name.split()[0]))
    async with request.app.state.sessionmaker() as db:
        row = (await db.execute(_HANDOFF_ROWS.where(Handoff.id == handoff_id))).one()
    return _handoff_row(*row)
