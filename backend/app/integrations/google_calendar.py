"""Google Calendar through OAuth 2.0, on a dedicated test calendar.

The refresh token comes from a one-time sign-in (python -m app.integrations.google_oauth).
Each idempotency key maps to a fixed event id, and the client looks for that event before
creating it, so a retry after a lost response finds the event instead of adding another.
"""

import asyncio
import base64
import hashlib
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.base import CalendarEvent, IntegrationError

API_URL = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a secret
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
)


def event_id_for(idempotency_key: str) -> str:
    """A valid Calendar event id (base32hex: 0-9 and a-v) derived from the key."""
    digest = hashlib.sha256(idempotency_key.encode()).digest()
    return base64.b32hexencode(digest).decode().rstrip("=").lower()


class _Conflict(Exception):
    pass


class GoogleCalendar:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        calendar_id: str,
        http: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._calendar_id = calendar_id
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = asyncio.Lock()

    async def busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, datetime]]:
        data = await self._call(
            "POST",
            "/freeBusy",
            json={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self._calendar_id}],
            },
        )
        calendar = ((data or {}).get("calendars") or {}).get(self._calendar_id, {})
        if calendar.get("errors"):
            raise IntegrationError(f"Google free/busy failed: {calendar['errors']}")
        return [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in calendar.get("busy", [])
        ]

    async def create_event(
        self, *, starts_at: datetime, ends_at: datetime, title: str, idempotency_key: str
    ) -> CalendarEvent:
        event_id = event_id_for(idempotency_key)
        existing = await self._call("GET", self._event_path(event_id), missing_ok=True)
        if existing is not None:
            return _event(existing)
        body = {
            "id": event_id,
            "summary": title,
            "start": {"dateTime": starts_at.isoformat()},
            "end": {"dateTime": ends_at.isoformat()},
        }
        try:
            created = await self._call("POST", self._events_path(), json=body)
        except _Conflict:
            created = await self._call("GET", self._event_path(event_id))
        return _event(created)

    async def move_event(
        self, event_id: str, *, starts_at: datetime, ends_at: datetime, idempotency_key: str
    ) -> CalendarEvent:
        # Setting the same start and end again changes nothing, so a retry is harmless.
        data = await self._call(
            "PATCH",
            self._event_path(event_id),
            json={
                "start": {"dateTime": starts_at.isoformat()},
                "end": {"dateTime": ends_at.isoformat()},
            },
        )
        return _event(data)

    async def delete_event(self, event_id: str) -> bool:
        return await self._call("DELETE", self._event_path(event_id), missing_ok=True) is not None

    def _events_path(self) -> str:
        return f"/calendars/{quote(self._calendar_id, safe='')}/events"

    def _event_path(self, event_id: str) -> str:
        return f"{self._events_path()}/{quote(event_id, safe='')}"

    async def _call(
        self, method: str, path: str, *, json: Any = None, missing_ok: bool = False
    ) -> dict[str, Any] | None:
        token = await self._access_token()
        try:
            response = await self._http.request(
                method, f"{API_URL}{path}", json=json, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(
                f"Google Calendar {method} failed: {type(exc).__name__}"
            ) from exc
        if response.status_code in (404, 410) and missing_ok:
            return None
        if response.status_code == 409:
            raise _Conflict
        if response.status_code >= 400:
            raise IntegrationError(
                f"Google Calendar {method} returned {response.status_code}: {_message(response)}"
            )
        if not response.content:
            return {}
        data: dict[str, Any] = response.json()
        return data

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token is not None and self._clock() < self._token_expires:
                return self._token
            try:
                response = await self._http.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                    },
                )
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Google sign-in failed: {type(exc).__name__}") from exc
            if response.status_code != 200:
                if _error_code(response) == "invalid_grant":
                    raise IntegrationError(
                        "The Google refresh token is expired or revoked. Run "
                        "`python -m app.integrations.google_oauth` again."
                    )
                raise IntegrationError(f"Google token refresh returned {response.status_code}")
            data = response.json()
            self._token = str(data["access_token"])
            self._token_expires = self._clock() + int(data.get("expires_in", 3600)) - 60
            return self._token


def _event(data: dict[str, Any] | None) -> CalendarEvent:
    if not data:
        raise IntegrationError("Google Calendar returned no event")
    return CalendarEvent(
        id=str(data["id"]),
        starts_at=datetime.fromisoformat(data["start"]["dateTime"]),
        ends_at=datetime.fromisoformat(data["end"]["dateTime"]),
        title=str(data.get("summary", "")),
    )


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", ""))[:200]
    except (ValueError, AttributeError):
        return response.text[:200]


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error")
    except ValueError:
        return None
    return error if isinstance(error, str) else None
