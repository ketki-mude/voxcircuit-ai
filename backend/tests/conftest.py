import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import WebSocketTestSession

from app.config import Settings
from app.live.base import LiveSession
from app.live.factory import EngineName, InputMode, LiveSessionFactory
from app.live.fake import FakeLiveSession, ScriptStep
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/voxcircuit_test"
)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """A freshly migrated test database. Its schema is dropped and rebuilt every run."""
    database = make_url(TEST_DATABASE_URL).database or ""
    if not database.endswith("_test"):
        raise RuntimeError(f"Refusing to reset {database!r}: test database names end in _test")
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
async def sessionmaker(db_engine: Engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Async sessions on the migrated test database, for service-level tests."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "gemini_api_key": None,
        "daily_call_cap": 10_000,
        "max_turns": 30,
        "max_call_seconds": 300,
        "app_secret_key": "test-secret-key-for-caller-tokens",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeSessions:
    """A LiveSessionFactory that hands out scripted FakeLiveSessions and remembers them."""

    def __init__(
        self, turns: Sequence[Sequence[ScriptStep]], *, audio_turn_bytes: int | None = None
    ) -> None:
        self._turns = turns
        self._audio_turn_bytes = audio_turn_bytes
        self.created: list[FakeLiveSession] = []

    def __call__(self, engine: EngineName, input_mode: InputMode) -> LiveSession:
        session = FakeLiveSession(self._turns, audio_turn_bytes=self._audio_turn_bytes)
        self.created.append(session)
        return session


class SessionsInOrder:
    """A LiveSessionFactory that gives each new call the next script."""

    def __init__(self, *scripts: Sequence[Sequence[ScriptStep]]) -> None:
        self._scripts = scripts
        self.created: list[FakeLiveSession] = []

    def __call__(self, engine: EngineName, input_mode: InputMode) -> LiveSession:
        session = FakeLiveSession(self._scripts[len(self.created)])
        self.created.append(session)
        return session


AppFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(db_engine: Engine) -> Iterator[AppFactory]:
    """Build a TestClient for an app with the given settings and live session factory."""
    clients: list[TestClient] = []

    def build(
        *, sessions: LiveSessionFactory | None = None, **setting_overrides: Any
    ) -> TestClient:
        app = create_app(make_settings(**setting_overrides), sessions)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def receive_until(ws: WebSocketTestSession, message_type: str) -> list[dict[str, Any]]:
    """Read server frames up to and including the first message of message_type.

    Binary frames (Maya's audio) are returned as {"type": "audio_frame", "data": bytes}.
    """
    received: list[dict[str, Any]] = []
    while True:
        frame = ws.receive()
        if frame["type"] == "websocket.close":
            raise AssertionError(f"socket closed before a {message_type!r} message: {received}")
        if frame.get("bytes") is not None:
            received.append({"type": "audio_frame", "data": frame["bytes"]})
            continue
        message: dict[str, Any] = json.loads(frame["text"])
        received.append(message)
        if message["type"] == message_type:
            return received


def of_type(messages: list[dict[str, Any]], message_type: str) -> list[dict[str, Any]]:
    return [m for m in messages if m["type"] == message_type]


START = {"type": "start", "engine": "fake", "input_mode": "text", "consent": True}
START_AUDIO = START | {"input_mode": "audio"}


def open_call(ws: WebSocketTestSession, start: dict[str, Any] = START) -> tuple[uuid.UUID, str]:
    """Start a call and read past the greeting. Returns the call id and caller token."""
    ws.send_json(start)
    started = receive_until(ws, "call_started")[-1]
    receive_until(ws, "turn_complete")
    return uuid.UUID(started["call_id"]), started["session_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


TEAM_PASSWORD = "correct horse battery staple"


def make_team_user(db_engine: Engine, role: str, *, disabled: bool = False) -> str:
    """A team user with TEAM_PASSWORD. Returns their (unique) email."""
    from sqlalchemy import insert

    from app.auth import hash_password
    from app.models import TeamUser

    email = f"{role}-{uuid.uuid4().hex[:8]}@northstar.example"
    with db_engine.begin() as conn:
        conn.execute(
            insert(TeamUser).values(
                id=uuid.uuid4(),
                email=email,
                name=f"{role.title()} Tester",
                role=role,
                password_hash=hash_password(TEAM_PASSWORD),
                disabled=disabled,
            )
        )
    return email


def team_headers(client: TestClient, db_engine: Engine, role: str) -> dict[str, str]:
    """Sign in a new team user with this role; return their Authorization header."""
    email = make_team_user(db_engine, role)
    response = client.post("/api/auth/login", json={"email": email, "password": TEAM_PASSWORD})
    assert response.status_code == 200, response.text
    return auth(response.json()["access_token"])
