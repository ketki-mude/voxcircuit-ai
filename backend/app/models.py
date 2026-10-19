import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'ended', 'failed')", name="status"),
        CheckConstraint("engine IN ('gemini', 'fake')", name="engine"),
        CheckConstraint("input_mode IN ('text', 'audio')", name="input_mode"),
        CheckConstraint("purpose IN ('live', 'evaluation')", name="purpose"),
        Index("ix_calls_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    end_reason: Mapped[str | None] = mapped_column(String(32))
    engine: Mapped[str] = mapped_column(String(16))
    input_mode: Mapped[str] = mapped_column(String(8))
    model: Mapped[str | None] = mapped_column(String(128))
    flagged: Mapped[bool] = mapped_column(server_default=false())
    # Evaluation calls are kept out of the control room's numbers.
    purpose: Mapped[str] = mapped_column(String(16), server_default=text("'live'"))
    # Set only by the verification service, never from anything the model says.
    verified_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """One finalized turn of text: a typed caller message or a transcript of speech."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("call_id", "seq", name="uq_messages_call_id_seq"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        CheckConstraint("source IN ('typed', 'transcription')", name="source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    interrupted: Mapped[bool] = mapped_column(server_default=false())
    # Assistant turns only: caller input finished -> first model output, in milliseconds.
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageLog(Base):
    """Usage recorded per model turn. Token counts are stored as the API reports them."""

    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    audio_seconds_in: Mapped[float] = mapped_column(Float, server_default=text("0"))
    audio_seconds_out: Mapped[float] = mapped_column(Float, server_default=text("0"))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    response_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only event log. A database trigger rejects UPDATE, DELETE and TRUNCATE.

    call_id has no foreign key so audit history outlives the rows it describes.
    Payloads carry ids and counts, never message text or personal details.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("actor IN ('caller', 'model', 'system', 'team')", name="actor"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    call_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    actor: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class ToolCall(Base):
    """One function call made by the model, with its validated arguments and result."""

    __tablename__ = "tool_calls"
    __table_args__ = (CheckConstraint("status IN ('ok', 'error')", name="status"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(8))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    """A booking the model asked for. Nothing is written until the caller approves it.

    ticket is the snapshot the caller saw (lead details and slot); the workflow books
    exactly that. At most one approval per call is pending at a time.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'cancelled', 'expired')", name="status"),
        CheckConstraint("action IN ('book', 'reschedule')", name="action"),
        Index(
            "uq_approvals_one_pending_per_call",
            "call_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    lead_id: Mapped[uuid.UUID]
    slot_id: Mapped[str] = mapped_column(String(16))
    ticket: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    """Lead details, written only by the booking workflow after the caller approves."""

    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('new_policy', 'switching_insurers', 'adding_vehicle')", name="reason"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    full_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), index=True)
    reason: Mapped[str] = mapped_column(String(32))
    vehicle_make: Mapped[str] = mapped_column(String(40))
    vehicle_year: Mapped[int] = mapped_column(Integer)
    zip_code: Mapped[str] = mapped_column(String(5))
    current_insurer: Mapped[str | None] = mapped_column(String(80))
    renewal_month: Mapped[str | None] = mapped_column(String(12))
    preferred_time: Mapped[str | None] = mapped_column(String(80))
    crm_contact_id: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Slot(Base):
    """A consultation time that is held or booked. The primary key prevents double booking."""

    __tablename__ = "slots"
    __table_args__ = (CheckConstraint("status IN ('held', 'booked', 'released')", name="status"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # e.g. "2026-10-20T13:30"
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id", ondelete="CASCADE"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'booked', 'failed', 'rescheduled')", name="status"),
        CheckConstraint("action IN ('book', 'reschedule')", name="action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approvals.id", ondelete="CASCADE"), unique=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    slot_id: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    calendar_event_id: Mapped[str | None] = mapped_column(String(64))
    replaces_appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BookingStep(Base):
    """One external write of the booking workflow. The idempotency key is sent to the
    external system and is unique here, so a retry can never create a duplicate."""

    __tablename__ = "booking_steps"
    __table_args__ = (
        UniqueConstraint("approval_id", "step", name="uq_booking_steps_approval_id_step"),
        CheckConstraint("step IN ('crm_upsert', 'calendar_event')", name="step"),
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8), server_default=text("'pending'"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    external_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Customer(Base):
    """A caller whose details reached the CRM. Returning callers verify against this."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VerificationAttempt(Base):
    """One verification challenge: a 6-digit code, 3 tries, 5 minutes.

    Stores a hash of the code and of the email, never either in plain text. customer_id is
    null when no customer has that email; the caller is not told which.
    """

    __tablename__ = "verification_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'verified', 'failed', 'expired')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    email_hash: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Handoff(Base):
    """A call that needs a licensed human. At most one open handoff per call."""

    __tablename__ = "handoffs"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'taken', 'closed')", name="status"),
        CheckConstraint(
            "reason IN ('caller_upset', 'asked_for_human', 'claim_or_complaint', "
            "'out_of_scope', 'repeated_correction', 'tool_failures', 'verification_failed', "
            "'booking_incomplete', 'model_request')",
            name="reason",
        ),
        Index(
            "uq_handoffs_one_open_per_call",
            "call_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    taken_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("team_users.id", ondelete="SET NULL")
    )


class PolicyEvent(Base):
    """A finalized assistant transcript that broke a deterministic policy rule."""

    __tablename__ = "policy_events"
    __table_args__ = (
        CheckConstraint(
            "rule IN ('price', 'approval_promise', 'coverage_advice', 'unconfirmed_booking')",
            name="rule",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    rule: Mapped[str] = mapped_column(String(32))
    excerpt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamUser(Base):
    """A Northstar team member who can use the control room."""

    __tablename__ = "team_users"
    __table_args__ = (CheckConstraint("role IN ('viewer', 'agent', 'admin')", name="role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    disabled: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('scripted', 'live')", name="mode"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'running'"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("team_users.id", ondelete="SET NULL")
    )
    scenario_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    passed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    task_success_rate: Mapped[float | None] = mapped_column(Float)
    handoff_accuracy: Mapped[float | None] = mapped_column(Float)
    unauthorized_writes: Mapped[int | None] = mapped_column(Integer)
    history_leaks: Mapped[int | None] = mapped_column(Integer)
    policy_flags: Mapped[int | None] = mapped_column(Integer)
    latency_p50_ms: Mapped[int | None] = mapped_column(Integer)
    latency_p95_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationResult(Base):
    """One scenario of a run: what was expected, what happened, and why it failed."""

    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    passed: Mapped[bool]
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actual: Mapped[dict[str, Any]] = mapped_column(JSONB)
    failures: Mapped[list[str]] = mapped_column(JSONB)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"))
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
