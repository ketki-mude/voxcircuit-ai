import json
import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
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


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "gemini_api_key": None,
        "daily_call_cap": 10_000,
        "max_turns": 30,
        "max_call_seconds": 300,
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
