import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    approvals,
    control_room,
    customers,
    evaluation,
    health,
    mock_routes,
    team_auth,
    verify,
    ws_call,
)
from app.auth import CallerTokens, TeamTokens
from app.booking.approvals import ApprovalService
from app.booking.workflow import BookingWorkflow
from app.calls.registry import CallRegistry
from app.config import Settings, get_settings
from app.customers import CustomerData
from app.db import create_db_engine, create_sessionmaker
from app.evaluation.service import EvaluationService
from app.integrations.base import CalendarClient, CrmClient
from app.integrations.google_calendar import GoogleCalendar
from app.integrations.hubspot import HubSpotCrm
from app.integrations.mock import MockCalendar, MockCrm
from app.live.factory import LiveSessionFactory, default_live_session_factory
from app.safety.handoff import HandoffService
from app.verification.service import VerificationService

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    live_session_factory: LiveSessionFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    http, calendar, crm = _integrations(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url)
        sessionmaker = create_sessionmaker(engine)
        app.state.sessionmaker = sessionmaker
        app.state.approvals = ApprovalService(
            sessionmaker,
            BookingWorkflow(sessionmaker, app.state.calendar, app.state.crm),
            app.state.registry,
            ttl_seconds=settings.approval_ttl_seconds,
        )
        app.state.verification = VerificationService(sessionmaker, app.state.registry)
        app.state.handoffs = HandoffService(sessionmaker)
        app.state.customer_data = CustomerData(
            sessionmaker, app.state.calendar, app.state.crm, app.state.registry
        )
        app.state.evaluation = EvaluationService(
            settings=settings,
            sessionmaker=sessionmaker,
            live_factory=app.state.live_session_factory,
        )
        await app.state.evaluation.mark_interrupted()
        yield
        await app.state.evaluation.stop()
        if http is not None:
            await http.aclose()
        await engine.dispose()

    app = FastAPI(title="VoxCircuit AI", lifespan=lifespan)
    app.state.settings = settings
    app.state.live_session_factory = live_session_factory or default_live_session_factory(settings)
    app.state.calendar = calendar
    app.state.crm = crm
    app.state.registry = CallRegistry()
    secret = _secret_key(settings)
    app.state.caller_tokens = CallerTokens(secret)
    app.state.team_tokens = TeamTokens(secret)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(ws_call.router)
    app.include_router(approvals.router)
    app.include_router(verify.router)
    app.include_router(customers.router)
    app.include_router(team_auth.router)
    app.include_router(control_room.router)
    app.include_router(evaluation.router)
    if settings.integrations_mode == "mock":
        app.include_router(mock_routes.router)
    return app


def _integrations(settings: Settings) -> tuple[httpx.AsyncClient | None, CalendarClient, CrmClient]:
    """The calendar and CRM for INTEGRATIONS_MODE, plus the HTTP client live mode shares."""
    if settings.integrations_mode == "mock":
        return None, MockCalendar(), MockCrm()
    live = settings.live_integrations()  # Raises if a setting is missing.
    http = httpx.AsyncClient(timeout=10)
    calendar = GoogleCalendar(
        client_id=live.google_client_id,
        client_secret=live.google_client_secret,
        refresh_token=live.google_refresh_token,
        calendar_id=live.google_calendar_id,
        http=http,
    )
    crm = HubSpotCrm(live.hubspot_token, http)
    return http, calendar, crm


def _secret_key(settings: Settings) -> str:
    key = settings.app_secret_key.get_secret_value() if settings.app_secret_key else ""
    if key:
        return key
    log.warning("APP_SECRET_KEY is not set; using a random key. Caller tokens reset on restart.")
    return secrets.token_urlsafe(32)


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx logs each request URL at INFO, and CRM lookup URLs contain email addresses.
logging.getLogger("httpx").setLevel(logging.WARNING)
app = create_app()
