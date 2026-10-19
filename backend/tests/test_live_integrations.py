"""HubSpot and Google Calendar clients against httpx.MockTransport: no network, no accounts."""

import base64
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.booking.workflow import BookingWorkflow
from app.integrations.base import IntegrationError
from app.integrations.google_calendar import GoogleCalendar, event_id_for
from app.integrations.google_oauth import authorization_url, exchange_code, pkce_pair
from app.integrations.hubspot import HubSpotCrm
from app.main import create_app
from tests.conftest import make_settings
from tests.helpers import approval_for, fresh_slot

Handler = Callable[[httpx.Request], httpx.Response]
CALENDAR_ID = "test-calendar@group.calendar.google.com"
START = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
END = datetime(2026, 10, 20, 13, 30, tzinfo=UTC)


def client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


# HubSpot


async def test_hubspot_upserts_by_email_with_standard_properties() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "COMPLETE", "results": [{"id": "501"}]})

    crm = HubSpotCrm("pat-token", client(handler))
    contact = await crm.upsert_contact(
        email="sam@example.com",
        properties={"full_name": "Sam Q Testperson", "zip_code": "02139", "reason": "new_policy"},
        idempotency_key="ignored",
    )

    assert contact.id == "501"
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", "/crm/v3/objects/contacts/batch/upsert")
    assert request.headers["Authorization"] == "Bearer pat-token"
    assert body(request) == {
        "inputs": [
            {
                "idProperty": "email",
                "id": "sam@example.com",
                "properties": {
                    "email": "sam@example.com",
                    "firstname": "Sam",
                    "lastname": "Q Testperson",
                    "zip": "02139",
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"message": "Too many requests"}),
        httpx.Response(200, json={"status": "COMPLETE", "results": [], "errors": ["bad"]}),
    ],
)
async def test_hubspot_errors_become_integration_errors(response: httpx.Response) -> None:
    crm = HubSpotCrm("secret-token", client(lambda request: response))
    with pytest.raises(IntegrationError) as error:
        await crm.upsert_contact(email="a@example.com", properties={}, idempotency_key="k")
    assert "secret-token" not in str(error.value)


async def test_hubspot_network_failure_is_an_integration_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(IntegrationError, match="ConnectError"):
        await HubSpotCrm("t", client(handler)).upsert_contact(
            email="a@example.com", properties={}, idempotency_key="k"
        )


async def test_hubspot_delete_looks_up_before_a_gdpr_delete() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            known = request.url.path.rsplit("/", 1)[-1] == "known@example.com"
            return httpx.Response(200, json={"id": "77"}) if known else httpx.Response(404)
        assert body(request) == {"objectId": "77"}
        return httpx.Response(204)

    crm = HubSpotCrm("t", client(handler))
    assert await crm.delete_contact(email="unknown@example.com") is False
    assert seen == ["GET /crm/v3/objects/contacts/unknown@example.com"]  # No GDPR call.
    assert await crm.delete_contact(email="known@example.com") is True
    assert seen[-1] == "POST /crm/v3/objects/contacts/gdpr-delete"


# Google Calendar


class FakeGoogle:
    """Just enough of the token endpoint and Calendar API, storing events by id."""

    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.token_requests = 0
        self.inserts = 0
        self.lose_next_insert_response = False
        self.insert_status: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            self.token_requests += 1
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == ["refresh-1"]
            return httpx.Response(
                200, json={"access_token": f"access-{self.token_requests}", "expires_in": 3600}
            )
        assert request.headers["Authorization"].startswith("Bearer access-")
        if path.endswith("/freeBusy"):
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        CALENDAR_ID: {
                            "busy": [
                                {"start": "2026-10-20T13:00:00Z", "end": "2026-10-20T13:30:00Z"}
                            ]
                        }
                    }
                },
            )
        event_id = path.rsplit("/", 1)[-1]
        if request.method == "GET":
            event = self.events.get(event_id)
            return httpx.Response(200, json=event) if event else httpx.Response(404)
        if request.method == "POST":
            self.inserts += 1
            if self.insert_status:
                return httpx.Response(self.insert_status)
            event = body(request)
            self.events[event["id"]] = event
            if self.lose_next_insert_response:
                self.lose_next_insert_response = False
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(200, json=event)
        if request.method == "PATCH":
            self.events[event_id] |= body(request)
            return httpx.Response(200, json=self.events[event_id])
        if request.method == "DELETE":
            return httpx.Response(204) if self.events.pop(event_id, None) else httpx.Response(410)
        raise AssertionError(f"unexpected {request.method} {path}")


def google(fake: FakeGoogle, clock: Callable[[], float] = lambda: 0.0) -> GoogleCalendar:
    return GoogleCalendar(
        client_id="client-1",
        client_secret="secret-1",
        refresh_token="refresh-1",
        calendar_id=CALENDAR_ID,
        http=client(fake),
        clock=clock,
    )


def test_event_ids_are_valid_and_stable() -> None:
    first = event_id_for("approval-1:calendar_event")
    assert re.fullmatch(r"[0-9a-v]{5,1024}", first)
    assert first == event_id_for("approval-1:calendar_event")
    assert first != event_id_for("approval-2:calendar_event")


async def test_google_access_token_is_cached_until_it_expires() -> None:
    fake, now = FakeGoogle(), [0.0]
    calendar = google(fake, clock=lambda: now[0])

    busy = await calendar.busy_intervals(START, END)
    await calendar.busy_intervals(START, END)
    assert fake.token_requests == 1
    now[0] = 3600.0  # Past expires_in minus the safety margin.
    await calendar.busy_intervals(START, END)
    assert fake.token_requests == 2
    assert busy == [(START, END)]


async def test_google_expired_refresh_token_says_how_to_fix_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    calendar = GoogleCalendar(
        client_id="c",
        client_secret="s",
        refresh_token="r",
        calendar_id=CALENDAR_ID,
        http=client(handler),
    )
    with pytest.raises(IntegrationError, match=r"app\.integrations\.google_oauth"):
        await calendar.busy_intervals(START, END)


async def test_google_create_event_is_idempotent() -> None:
    fake = FakeGoogle()
    calendar = google(fake)

    first = await calendar.create_event(
        starts_at=START, ends_at=END, title="Northstar consultation: Sam", idempotency_key="a:1"
    )
    again = await calendar.create_event(
        starts_at=START, ends_at=END, title="Northstar consultation: Sam", idempotency_key="a:1"
    )

    assert first == again
    assert first.id == event_id_for("a:1")
    assert (first.starts_at, first.ends_at) == (START, END)
    assert fake.inserts == 1  # The second call found the event and created nothing.


async def test_google_insert_conflict_returns_the_existing_event() -> None:
    fake = FakeGoogle()
    calendar = google(fake)
    event_id = event_id_for("a:1")
    fake.insert_status = 409
    stored = {
        "id": event_id,
        "summary": "x",
        "start": {"dateTime": START.isoformat()},
        "end": {"dateTime": END.isoformat()},
    }

    original_call = fake.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        response = original_call(request)
        if request.method == "POST":
            fake.events[event_id] = stored  # Someone else created it in between.
        return response

    calendar = GoogleCalendar(
        client_id="client-1",
        client_secret="secret-1",
        refresh_token="refresh-1",
        calendar_id=CALENDAR_ID,
        http=client(handler),
    )
    event = await calendar.create_event(
        starts_at=START, ends_at=END, title="x", idempotency_key="a:1"
    )
    assert event.id == event_id


async def test_google_move_and_delete() -> None:
    fake = FakeGoogle()
    calendar = google(fake)
    created = await calendar.create_event(
        starts_at=START, ends_at=END, title="t", idempotency_key="k"
    )
    later = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)

    moved = await calendar.move_event(
        created.id, starts_at=later, ends_at=later, idempotency_key="m"
    )

    assert moved.starts_at == later
    assert await calendar.delete_event(created.id) is True
    assert await calendar.delete_event(created.id) is False


async def test_booking_with_live_clients_survives_a_lost_response(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    fake = FakeGoogle()
    fake.lose_next_insert_response = True  # Google stores the event; the reply never arrives.

    def hubspot(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "501"}]})

    workflow = BookingWorkflow(sessionmaker, google(fake), HubSpotCrm("t", client(hubspot)))
    outcome = await workflow.run(await approval_for(sessionmaker, fresh_slot()))

    assert outcome.success
    assert [(s.step, s.attempts) for s in outcome.steps] == [
        ("crm_upsert", 1),
        ("calendar_event", 2),
    ]
    assert len(fake.events) == 1 and fake.inserts == 1  # The retry found it: no duplicate.


# Wiring and OAuth setup


def test_live_mode_needs_every_setting() -> None:
    with pytest.raises(ValueError, match="HUBSPOT_PRIVATE_APP_TOKEN, GOOGLE_OAUTH_CLIENT_ID"):
        create_app(make_settings(integrations_mode="live"))


def test_live_mode_uses_the_real_clients_and_hides_mock_routes() -> None:
    app = create_app(
        make_settings(
            integrations_mode="live",
            hubspot_private_app_token="t",
            google_oauth_client_id="c",
            google_oauth_client_secret="s",
            google_oauth_refresh_token="r",
            google_calendar_id=CALENDAR_ID,
        )
    )
    assert isinstance(app.state.calendar, GoogleCalendar)
    assert isinstance(app.state.crm, HubSpotCrm)
    assert not any(getattr(route, "path", "").startswith("/mock") for route in app.routes)


def test_oauth_setup_uses_pkce_and_asks_for_a_refresh_token() -> None:
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode() and 43 <= len(verifier) <= 128

    url = urlparse(authorization_url("client-1", "http://127.0.0.1:5555", challenge, "state-1"))
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert (url.netloc, url.path) == ("accounts.google.com", "/o/oauth2/v2/auth")
    assert query == {
        "client_id": "client-1",
        "redirect_uri": "http://127.0.0.1:5555",
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar.events "
        "https://www.googleapis.com/auth/calendar.freebusy",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-1",
        "access_type": "offline",
        "prompt": "consent",
    }


def test_oauth_code_exchange() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form == {
            "grant_type": "authorization_code",
            "client_id": "c",
            "client_secret": "s",
            "code": "code-1",
            "code_verifier": "v",
            "redirect_uri": "http://127.0.0.1:5555",
        }
        return httpx.Response(200, json={"refresh_token": "refresh-xyz"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        tokens = exchange_code(
            http,
            client_id="c",
            client_secret="s",
            code="code-1",
            verifier="v",
            redirect_uri="http://127.0.0.1:5555",
        )
    assert tokens["refresh_token"] == "refresh-xyz"
