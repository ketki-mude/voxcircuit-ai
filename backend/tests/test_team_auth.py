"""Team sign-in, roles on control-room endpoints, and the create-user command."""

import io
import uuid

import pytest
from sqlalchemy import Engine, select

from app import cli
from app.auth import hash_password, verify_password
from app.config import get_settings
from app.live.fake import scripted_reply
from app.models import TeamUser
from tests.conftest import (
    TEAM_PASSWORD,
    TEST_DATABASE_URL,
    AppFactory,
    FakeSessions,
    auth,
    make_team_user,
    open_call,
    team_headers,
)


def test_password_hashes_verify_and_differ() -> None:
    first, second = hash_password("s3cret-enough"), hash_password("s3cret-enough")
    assert first != second  # Random salt.
    assert verify_password("s3cret-enough", first)
    assert not verify_password("wrong", first)
    assert not verify_password("s3cret-enough", "not-a-hash")


def test_login_returns_a_token_and_the_user(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client()
    email = make_team_user(db_engine, "agent")

    response = client.post(
        "/api/auth/login", json={"email": email.upper(), "password": TEAM_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == email and body["user"]["role"] == "agent"
    me = client.get("/api/auth/me", headers=auth(body["access_token"]))
    assert me.json()["email"] == email


@pytest.mark.parametrize("problem", ["wrong_password", "unknown_email", "disabled"])
def test_login_failures_look_the_same(
    make_client: AppFactory, db_engine: Engine, problem: str
) -> None:
    client = make_client()
    email = make_team_user(db_engine, "admin", disabled=problem == "disabled")
    if problem == "unknown_email":
        email = "nobody@northstar.example"
    password = "wrong password!" if problem == "wrong_password" else TEAM_PASSWORD

    response = client.post("/api/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong email or password"}


def test_caller_and_team_tokens_are_not_interchangeable(
    make_client: AppFactory, db_engine: Engine
) -> None:
    client = make_client(sessions=FakeSessions([scripted_reply("Hi.")]))
    team = team_headers(client, db_engine, "admin")

    with client.websocket_connect("/ws/call") as ws:
        _, caller_token = open_call(ws)
        assert client.get("/api/auth/me", headers=auth(caller_token)).status_code == 401
        assert (
            client.post("/api/verify/check", json={"code": "123456"}, headers=team).status_code
            == 401
        )


ROLE_CHECKS = [
    # (method, path, minimum role)
    ("GET", "/api/control-room/summary", "viewer"),
    ("GET", "/api/calls", "viewer"),
    ("GET", "/api/handoffs", "viewer"),
    ("GET", "/api/evaluation/runs", "viewer"),
    ("GET", f"/api/calls/{uuid.UUID(int=0)}", "agent"),
    ("POST", f"/api/handoffs/{uuid.UUID(int=0)}/take-over", "agent"),
    ("POST", "/api/evaluate", "admin"),
]
RANK = {"viewer": 0, "agent": 1, "admin": 2}


@pytest.mark.parametrize("role", ["viewer", "agent", "admin"])
def test_control_room_endpoints_check_roles(
    make_client: AppFactory, db_engine: Engine, role: str
) -> None:
    client = make_client()
    headers = team_headers(client, db_engine, role)
    for method, path, minimum in ROLE_CHECKS:
        body = {"mode": "live"} if path == "/api/evaluate" else None
        assert client.request(method, path, json=body).status_code == 401, path
        status = client.request(method, path, json=body, headers=headers).status_code
        if RANK[role] < RANK[minimum]:
            assert status == 403, (role, path)
        else:
            # Allowed through auth: the rest is the endpoint's own answer (for example a 404
            # for the made-up id, or 400 because live evaluation needs a key).
            assert status in (200, 202, 400, 404), (role, path, status)


def test_create_user_command(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    email = f"ada-{uuid.uuid4().hex[:6]}@northstar.example"
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO("a long enough password\n"))
        cli.main(
            [
                "create-user",
                "--email",
                email,
                "--name",
                "Ada",
                "--role",
                "admin",
                "--password-stdin",
            ]
        )
        assert f"Created admin {email}." in capsys.readouterr().out

        monkeypatch.setattr("sys.stdin", io.StringIO("a long enough password\n"))
        with pytest.raises(SystemExit, match="already exists"):
            cli.main(
                [
                    "create-user",
                    "--email",
                    email,
                    "--name",
                    "Ada",
                    "--role",
                    "admin",
                    "--password-stdin",
                ]
            )
        monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
        with pytest.raises(SystemExit, match="at least 12 characters"):
            cli.main(
                [
                    "create-user",
                    "--email",
                    "x@example.com",
                    "--name",
                    "X",
                    "--role",
                    "viewer",
                    "--password-stdin",
                ]
            )
    finally:
        get_settings.cache_clear()

    with db_engine.connect() as conn:
        user = conn.execute(select(TeamUser).where(TeamUser.email == email)).one()
    assert user.role == "admin" and verify_password("a long enough password", user.password_hash)
