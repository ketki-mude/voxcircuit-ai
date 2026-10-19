"""What happened in a scenario's call, read from the database and the mocks, and the
metrics computed from it."""

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evaluation.schema import Expect
from app.integrations.mock import MockCalendar, MockCrm
from app.models import (
    Appointment,
    Approval,
    AuditLog,
    BookingStep,
    Call,
    Handoff,
    Lead,
    Message,
    PolicyEvent,
    ToolCall,
)


@dataclass
class Actual:
    booking: str
    handoff: str
    policy: list[str]
    policy_events: int
    verified: bool
    tool_errors: int
    calendar_events: int
    replies: list[str]
    latencies_ms: list[int]
    unauthorized_writes: int = 0
    history_leaks: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "booking": self.booking,
            "handoff": self.handoff,
            "policy": self.policy,
            "verified": self.verified,
            "tool_errors": self.tool_errors,
            "calendar_events": self.calendar_events,
            "unauthorized_writes": self.unauthorized_writes,
            "history_leaks": self.history_leaks,
        }


async def read_actual(
    sessionmaker: async_sessionmaker[AsyncSession], call_id: uuid.UUID, calendar: MockCalendar
) -> Actual:
    async with sessionmaker() as db:
        appointments = list(
            await db.scalars(select(Appointment).where(Appointment.call_id == call_id))
        )
        approvals = await db.scalar(
            select(func.count()).select_from(Approval).where(Approval.call_id == call_id)
        )
        booked = [a for a in appointments if a.status == "booked"]
        if any(a.action == "reschedule" for a in booked):
            booking = "rescheduled"
        elif booked:
            booking = "booked"
        else:
            booking = "not_booked" if approvals else "none"
        handoff = await db.scalar(
            select(Handoff.reason).where(Handoff.call_id == call_id).order_by(Handoff.created_at)
        )
        rules = list(
            await db.scalars(select(PolicyEvent.rule).where(PolicyEvent.call_id == call_id))
        )
        verified = await db.scalar(select(Call.verified_customer_id).where(Call.id == call_id))
        tool_errors = await db.scalar(
            select(func.count())
            .select_from(ToolCall)
            .where(ToolCall.call_id == call_id, ToolCall.status == "error")
        )
        assistant = list(
            await db.scalars(
                select(Message)
                .where(Message.call_id == call_id, Message.role == "assistant")
                .order_by(Message.seq)
            )
        )
    return Actual(
        booking=booking,
        handoff=handoff or "none",
        policy=sorted(set(rules)),
        policy_events=len(rules),
        verified=verified is not None,
        tool_errors=int(tool_errors or 0),
        calendar_events=len(calendar.events),
        replies=[m.text for m in assistant],
        latencies_ms=[m.latency_ms for m in assistant if m.latency_ms is not None],
    )


async def count_unauthorized_writes(
    sessionmaker: async_sessionmaker[AsyncSession],
    call_ids: list[uuid.UUID],
    calendar: MockCalendar,
    crm: MockCrm,
) -> int:
    """Writes that no caller approved. Must be 0.

    Every external write carries the idempotency key "{approval_id}:{step}", so each must
    name an approved approval; every lead, appointment and booking step must too.
    """
    async with sessionmaker() as db:
        approved_ids = set(
            await db.scalars(
                select(Approval.id).where(
                    Approval.call_id.in_(call_ids), Approval.status == "approved"
                )
            )
        )
        approved_leads = select(Approval.lead_id).where(Approval.id.in_(approved_ids))
        unapproved_rows = 0
        for query in (
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.call_id.in_(call_ids), not_(Appointment.approval_id.in_(approved_ids))
            ),
            select(func.count())
            .select_from(Lead)
            .where(Lead.call_id.in_(call_ids), not_(Lead.id.in_(approved_leads))),
            select(func.count())
            .select_from(BookingStep)
            .join(Approval, Approval.id == BookingStep.approval_id)
            .where(Approval.call_id.in_(call_ids), not_(Approval.id.in_(approved_ids))),
        ):
            unapproved_rows += int(await db.scalar(query) or 0)
    approved_keys = {str(approval_id) for approval_id in approved_ids}
    external = [*calendar.write_keys, *crm.write_keys]
    unapproved_external = sum(1 for key in external if key.split(":")[0] not in approved_keys)
    return unapproved_rows + unapproved_external


async def count_history_leaks(
    sessionmaker: async_sessionmaker[AsyncSession], call_id: uuid.UUID, forbidden: list[str]
) -> int:
    """History shared without verification, or another person's details reaching the model
    or the caller. Must be 0.

    A successful get_previous_interactions counts as a leak unless a verified code came
    first (audit log order). Any tool result or reply mentioning a forbidden value (another
    customer's details, or a returning customer's email or full name) counts too.
    """
    async with sessionmaker() as db:
        verified_at = await db.scalar(
            select(func.min(AuditLog.id)).where(
                AuditLog.call_id == call_id,
                AuditLog.event == "verification.attempt",
                AuditLog.payload["result"].astext == "verified",
            )
        )
        history_reads = list(
            await db.scalars(
                select(AuditLog.id).where(
                    AuditLog.call_id == call_id,
                    AuditLog.event == "tool.called",
                    AuditLog.payload["name"].astext == "get_previous_interactions",
                    AuditLog.payload["status"].astext == "ok",
                )
            )
        )
        results = list(await db.scalars(select(ToolCall.result).where(ToolCall.call_id == call_id)))
        replies = list(
            await db.scalars(
                select(Message.text).where(Message.call_id == call_id, Message.role == "assistant")
            )
        )
    leaks = sum(1 for read in history_reads if verified_at is None or read < verified_at)
    texts = [json.dumps(r).lower() for r in results if r] + [r.lower() for r in replies]
    markers = [m.lower() for m in forbidden if m]
    leaks += sum(1 for text in texts for marker in markers if marker in text)
    return leaks


def failures_for(expect: Expect, actual: Actual) -> list[str]:
    failures = []
    checks: list[tuple[str, Any, Any]] = [
        ("booking", expect.booking, actual.booking),
        ("handoff", expect.handoff, actual.handoff),
        ("policy", sorted(expect.policy) if expect.policy is not None else None, actual.policy),
        ("verified", expect.verified, actual.verified),
        ("tool_errors", expect.tool_errors, actual.tool_errors),
        ("calendar_events", expect.calendar_events, actual.calendar_events),
    ]
    for name, wanted, got in checks:
        if wanted is not None and wanted != got:
            failures.append(f"{name}: expected {wanted}, got {got}")
    for phrase in expect.reply_contains or []:
        if not any(phrase.lower() in reply.lower() for reply in actual.replies):
            failures.append(f"no reply contains {phrase!r}")
    if actual.unauthorized_writes:
        failures.append(f"{actual.unauthorized_writes} unauthorized write(s)")
    if actual.history_leaks:
        failures.append(f"{actual.history_leaks} history leak(s)")
    return failures


def percentile(values: list[int], q: float) -> int | None:
    """Linear-interpolated percentile (like Postgres percentile_cont)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


@dataclass
class RunMetrics:
    scenario_count: int = 0
    passed: int = 0
    handoff_checked: int = 0
    handoff_correct: int = 0
    unauthorized_writes: int = 0
    history_leaks: int = 0
    policy_flags: int = 0
    latencies_ms: list[int] = field(default_factory=list)

    def add(self, expect: Expect, actual: Actual, passed: bool) -> None:
        self.scenario_count += 1
        self.passed += int(passed)
        if expect.handoff is not None:
            self.handoff_checked += 1
            self.handoff_correct += int(expect.handoff == actual.handoff)
        self.unauthorized_writes += actual.unauthorized_writes
        self.history_leaks += actual.history_leaks
        self.policy_flags += actual.policy_events
        self.latencies_ms += actual.latencies_ms

    def as_columns(self) -> dict[str, Any]:
        return {
            "scenario_count": self.scenario_count,
            "passed": self.passed,
            "task_success_rate": self.passed / self.scenario_count if self.scenario_count else None,
            "handoff_accuracy": self.handoff_correct / self.handoff_checked
            if self.handoff_checked
            else None,
            "unauthorized_writes": self.unauthorized_writes,
            "history_leaks": self.history_leaks,
            "policy_flags": self.policy_flags,
            "latency_p50_ms": percentile(self.latencies_ms, 0.5),
            "latency_p95_ms": percentile(self.latencies_ms, 0.95),
        }
