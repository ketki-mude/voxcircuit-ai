import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, insert, text
from sqlalchemy.exc import DBAPIError

from app.models import AuditLog, Base


def test_migrations_match_models(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET event = 'tampered'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
def test_audit_logs_are_append_only(db_engine: Engine, statement: str) -> None:
    with db_engine.begin() as conn:
        conn.execute(insert(AuditLog).values(actor="system", event="test.row", payload={}))

    with pytest.raises(DBAPIError, match="append-only"), db_engine.begin() as conn:
        conn.execute(text(statement))
