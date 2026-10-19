"""Inspect the mock calendar and CRM, and switch their failure modes. Mock mode only."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.integrations.base import FaultMode
from app.integrations.mock import MockCalendar, MockCrm

router = APIRouter(prefix="/mock", tags=["mock integrations"])


class FaultsBody(BaseModel):
    mode: FaultMode


def _calendar(request: Request) -> MockCalendar:
    calendar: MockCalendar = request.app.state.calendar
    return calendar


def _crm(request: Request) -> MockCrm:
    crm: MockCrm = request.app.state.crm
    return crm


@router.get("/calendar/events")
async def calendar_events(request: Request) -> list[dict[str, Any]]:
    events = sorted(_calendar(request).events.values(), key=lambda e: e.starts_at)
    return [event.model_dump(mode="json") for event in events]


@router.put("/calendar/faults")
async def calendar_faults(body: FaultsBody, request: Request) -> dict[str, str]:
    _calendar(request).faults.mode = body.mode
    return {"mode": body.mode}


@router.get("/crm/contacts")
async def crm_contacts(request: Request) -> list[dict[str, Any]]:
    return [contact.model_dump() for contact in _crm(request).contacts.values()]


@router.put("/crm/faults")
async def crm_faults(body: FaultsBody, request: Request) -> dict[str, str]:
    _crm(request).faults.mode = body.mode
    return {"mode": body.mode}
