"""Initial schema: calls, messages, usage_logs, audit_logs.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("end_reason", sa.String(32), nullable=True),
        sa.Column("engine", sa.String(16), nullable=False),
        sa.Column("input_mode", sa.String(8), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("flagged", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'ended', 'failed')", name=op.f("ck_calls_status")),
        sa.CheckConstraint("engine IN ('gemini', 'fake')", name=op.f("ck_calls_engine")),
        sa.CheckConstraint("input_mode IN ('text', 'audio')", name=op.f("ck_calls_input_mode")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calls")),
    )
    op.create_index("ix_calls_started_at", "calls", ["started_at"])

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("interrupted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_role")
        ),
        sa.CheckConstraint("source IN ('typed', 'transcription')", name=op.f("ck_messages_source")),
        sa.ForeignKeyConstraint(
            ["call_id"], ["calls.id"], name=op.f("fk_messages_call_id_calls"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("call_id", "seq", name="uq_messages_call_id_seq"),
    )

    op.create_table(
        "usage_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("audio_seconds_in", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("audio_seconds_out", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("response_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "estimated_cost_usd", sa.Numeric(12, 6), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["call_id"], ["calls.id"], name=op.f("fk_usage_logs_call_id_calls"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_logs")),
    )
    op.create_index(op.f("ix_usage_logs_call_id"), "usage_logs", ["call_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("actor", sa.String(16), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "actor IN ('caller', 'model', 'system', 'team')", name=op.f("ck_audit_logs_actor")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_call_id"), "audit_logs", ["call_id"])

    # Append-only: reject every UPDATE, DELETE and TRUNCATE on audit_logs.
    op.execute(
        """
        CREATE FUNCTION audit_logs_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only (% rejected)', TG_OP;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION audit_logs_reject_change();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_reject_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_reject_change()")
    op.drop_index(op.f("ix_audit_logs_call_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_usage_logs_call_id"), table_name="usage_logs")
    op.drop_table("usage_logs")
    op.drop_table("messages")
    op.drop_index("ix_calls_started_at", table_name="calls")
    op.drop_table("calls")
