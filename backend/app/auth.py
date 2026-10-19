"""Authentication.

Callers: when a call starts, the caller's browser gets a short-lived signed token naming that
call. REST endpoints that act on a call (approvals, verification, delete my data) require
it, so one caller can never act on another caller's call.

Team users: control-room endpoints require a team token from POST /api/auth/login, and a
role of at least viewer, agent or admin. Both token kinds are signed with APP_SECRET_KEY and
carry a "typ" claim, so neither can stand in for the other.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import TeamUser

_ALGORITHM = "HS256"
TEAM_TOKEN_TTL = timedelta(hours=8)

Role = Literal["viewer", "agent", "admin"]
ROLE_RANK: dict[str, int] = {"viewer": 0, "agent": 1, "admin": 2}


class InvalidToken(Exception):
    pass


class _Signer:
    typ: str

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def _encode(self, subject: str, ttl: timedelta, **claims: Any) -> str:
        now = datetime.now(UTC)
        payload = {"sub": subject, "typ": self.typ, "iat": now, "exp": now + ttl, **claims}
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def _subject(self, token: str) -> uuid.UUID:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": ["exp", "sub", "typ"]},
            )
            if claims["typ"] != self.typ:
                raise InvalidToken(f"not a {self.typ} token")
            return uuid.UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError) as exc:
            raise InvalidToken(str(exc)) from exc


class CallerTokens(_Signer):
    typ = "caller"

    def issue(self, call_id: uuid.UUID, *, ttl_seconds: int) -> str:
        return self._encode(str(call_id), timedelta(seconds=ttl_seconds))

    def verify(self, token: str) -> uuid.UUID:
        """The call id the token was issued for."""
        return self._subject(token)


class TeamTokens(_Signer):
    typ = "team"

    def issue(self, user: TeamUser) -> str:
        return self._encode(str(user.id), TEAM_TOKEN_TTL, role=user.role)

    def verify(self, token: str) -> uuid.UUID:
        """The team user id. The role is re-read from the database on every request."""
        return self._subject(token)


# Passwords: scrypt from the standard library, with a random salt per password.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=len(expected), **_SCRYPT)
    return hmac.compare_digest(digest, expected)


# Checked when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))

_bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def caller_call_id(request: Request, credentials: Credentials) -> uuid.UUID:
    """FastAPI dependency: the call id from a valid caller token, or 401."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Caller token required")
    tokens: CallerTokens = request.app.state.caller_tokens
    try:
        return tokens.verify(credentials.credentials)
    except InvalidToken as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Caller token is invalid or expired"
        ) from exc


def team_member(minimum: Role) -> Callable[[Request, Credentials], Awaitable[TeamUser]]:
    """FastAPI dependency factory: the signed-in team user, if their role is high enough."""

    async def dependency(request: Request, credentials: Credentials) -> TeamUser:
        if credentials is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
        tokens: TeamTokens = request.app.state.team_tokens
        try:
            user_id = tokens.verify(credentials.credentials)
        except InvalidToken as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Session expired. Sign in again."
            ) from exc
        async with request.app.state.sessionmaker() as db:
            user: TeamUser | None = await db.get(TeamUser, user_id)
        if user is None or user.disabled:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This needs the {minimum} role")
        return user

    return dependency


Viewer = Annotated[TeamUser, Depends(team_member("viewer"))]
Agent = Annotated[TeamUser, Depends(team_member("agent"))]
Admin = Annotated[TeamUser, Depends(team_member("admin"))]
