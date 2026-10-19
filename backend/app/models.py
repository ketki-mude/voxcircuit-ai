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
