"""Read-only queries for the control room. All numbers come from the database."""

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calls.recorder import OFFLINE_EVALUATION
from app.models import Appointment, Call, Handoff, Message, PolicyEvent, UsageLog

LIVE = Call.purpose == "live"


async def _count(db: AsyncSession, query: Select[Any]) -> int:
    return int(await db.scalar(query) or 0)


async def latency_percentiles(
    db: AsyncSession, *where: ColumnElement[bool]
) -> tuple[int | None, int | None, int]:
    """p50, p95 and sample count of assistant-turn latency for matching calls."""
    row = (
        await db.execute(
            select(
                func.percentile_cont(0.5).within_group(Message.latency_ms),
                func.percentile_cont(0.95).within_group(Message.latency_ms),
                func.count(Message.latency_ms),
            )
            .join(Call, Call.id == Message.call_id)
            .where(Message.role == "assistant", Message.latency_ms.is_not(None), *where)
        )
    ).one()
    p50, p95, samples = row
    return (
        round(p50) if p50 is not None else None,
        round(p95) if p95 is not None else None,
        int(samples),
    )


async def summary(db: AsyncSession, *, since: datetime) -> dict[str, Any]:
    """Business metrics count live calls only; usage counts every call that used quota."""
    calls_today = await _count(
        db, select(func.count()).select_from(Call).where(LIVE, Call.started_at >= since)
    )
    booked_today = await _count(
        db,
        select(func.count())
        .select_from(Appointment)
        .join(Call, Call.id == Appointment.call_id)
        .where(LIVE, Appointment.status == "booked", Appointment.created_at >= since),
    )
    needs_human = await _count(
        db,
        select(func.count())
        .select_from(Handoff)
        .join(Call, Call.id == Handoff.call_id)
        .where(LIVE, Handoff.status == "open"),
    )
    policy_flags_today = await _count(
        db,
        select(func.count())
        .select_from(PolicyEvent)
        .join(Call, Call.id == PolicyEvent.call_id)
        .where(LIVE, PolicyEvent.created_at >= since),
    )
    capped_calls_today = await _count(
        db,
        select(func.count())
        .select_from(Call)
        .where(Call.started_at >= since, not_(OFFLINE_EVALUATION)),
    )
    cost, seconds_in, seconds_out = (
        await db.execute(
            select(
                func.coalesce(func.sum(UsageLog.estimated_cost_usd), 0),
                func.coalesce(func.sum(UsageLog.audio_seconds_in), 0),
                func.coalesce(func.sum(UsageLog.audio_seconds_out), 0),
            ).where(UsageLog.created_at >= since)
        )
    ).one()
    p50, p95, samples = await latency_percentiles(db, LIVE, Message.created_at >= since)
    return {
        "metrics": {
            "calls_today": calls_today,
            "booked_today": booked_today,
            "needs_human": needs_human,
            "policy_flags_today": policy_flags_today,
        },
        "usage": {
            "calls_today": capped_calls_today,
            "estimated_cost_usd_today": float(cost),
            "audio_minutes_in_today": round(float(seconds_in) / 60, 2),
            "audio_minutes_out_today": round(float(seconds_out) / 60, 2),
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "latency_samples": samples,
        },
    }


def call_list_query(purpose: str) -> Select[Any]:
    """Calls, newest first, with per-call counts as correlated subqueries."""
    user_turns = (
        select(func.count())
        .where(Message.call_id == Call.id, Message.role == "user")
        .scalar_subquery()
    )
    booked = (
        select(func.count())
        .where(Appointment.call_id == Call.id, Appointment.status == "booked")
        .scalar_subquery()
    )
    handoff_reason = (
        select(Handoff.reason).where(Handoff.call_id == Call.id).limit(1).scalar_subquery()
    )
    policy_flags = select(func.count()).where(PolicyEvent.call_id == Call.id).scalar_subquery()
    cost = (
        select(func.coalesce(func.sum(UsageLog.estimated_cost_usd), 0))
        .where(UsageLog.call_id == Call.id)
        .scalar_subquery()
    )
    query = select(
        Call,
        user_turns.label("user_turns"),
        booked.label("booked"),
        handoff_reason.label("handoff_reason"),
        policy_flags.label("policy_flags"),
        cost.label("cost"),
    ).order_by(Call.started_at.desc())
    if purpose != "all":
        query = query.where(Call.purpose == purpose)
    return query
