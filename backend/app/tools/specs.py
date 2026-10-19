"""Function tools offered to the model.

Each tool's arguments are a Pydantic model: its JSON schema is what the model sees, and
the same model validates every call. Unknown fields are rejected, so the model cannot pass
data the spec forbids collecting (driver's license, SSN, VIN, payment details, date of
birth); string values are also checked for those patterns.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.booking.schemas import Action, Reason
from app.booking.slots import SLOT_ID_PATTERN

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"
UUID_PATTERN = r"^[0-9a-fA-F-]{36}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

Month = Literal[
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

_SENSITIVE = {
    "a Social Security number": re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"),
    "a VIN": re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE),
    "a payment card number": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}
_DATE_OF_BIRTH = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})\b"
)


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckAvailabilityArgs(ToolArgs):
    """Find open consultation times on one day."""

    date: str = Field(pattern=DATE_PATTERN, description="The day to check, as YYYY-MM-DD.")


class LeadArgs(ToolArgs):
    """Details the caller just gave or corrected. Pass only the fields you have."""

    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
    reason: Reason | None = Field(default=None, description="Why the caller wants a consultation.")
    vehicle_make: str | None = Field(
        default=None, min_length=2, max_length=40, pattern=r"^[A-Za-z][A-Za-z .&'-]*$"
    )
    vehicle_year: int | None = Field(default=None, ge=1950, le=2100)
    zip_code: str | None = Field(default=None, pattern=r"^\d{5}$", description="US ZIP code.")
    current_insurer: str | None = Field(default=None, max_length=80)
    renewal_month: Month | None = None
    preferred_time: str | None = Field(
        default=None, max_length=80, description="The caller's preference, in their words."
    )

    @model_validator(mode="after")
    def _reject_sensitive_data(self) -> "LeadArgs":
        for name, value in self:
            if not isinstance(value, str):
                continue
            for what, pattern in _SENSITIVE.items():
                if pattern.search(value):
                    raise ValueError(f"{name} looks like {what}. Never collect it.")
            if name != "preferred_time" and _DATE_OF_BIRTH.search(value):
                raise ValueError(f"{name} looks like a date of birth. Never collect it.")
        if self.vehicle_year is not None and self.vehicle_year > date.today().year + 1:
            raise ValueError("vehicle_year is in the future.")
        return self


class RequestBookingArgs(ToolArgs):
    """Ask the caller to approve a booking. This does not book anything by itself."""

    slot_id: str = Field(pattern=SLOT_ID_PATTERN, description="A slot_id from check_availability.")
    lead_id: str = Field(
        pattern=UUID_PATTERN, description="The lead_id from create_or_update_lead."
    )
    action: Action = Field(
        default="book", description='"reschedule" moves an appointment booked earlier.'
    )


class StartVerificationArgs(ToolArgs):
    """Send a returning caller a code for the email they have on file."""

    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)


class NoArgs(ToolArgs):
    """No arguments."""


class HandoffArgs(ToolArgs):
    """Hand the call to a licensed human agent."""

    reason: Literal[
        "caller_upset", "asked_for_human", "claim_or_complaint", "out_of_scope", "other"
    ]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[ToolArgs]

    def parameters_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "check_availability",
        "Find open consultation times with a licensed agent on one day. Read-only.",
        CheckAvailabilityArgs,
    ),
    ToolSpec(
        "create_or_update_lead",
        "Record details the caller gave or corrected. Keeps a draft only: nothing is saved "
        "until the caller approves a booking. Returns the lead_id and missing fields.",
        LeadArgs,
    ),
    ToolSpec(
        "request_booking",
        "Show the caller a booking ticket to approve or cancel on screen. Creates a pending "
        "approval only; the backend books after the caller approves.",
        RequestBookingArgs,
    ),
    ToolSpec(
        "start_verification",
        "For a caller who says they have called before: send a 6-digit code for the email "
        "on file. The caller types it on screen; never ask them to say it.",
        StartVerificationArgs,
    ),
    ToolSpec(
        "get_previous_interactions",
        "A short summary of a verified returning caller's history: first name, vehicle "
        "and last appointment. Refused unless the backend has verified the caller.",
        NoArgs,
    ),
    ToolSpec(
        "handoff_to_human",
        "Hand the call to a licensed human agent: the caller is upset, asks for a person, "
        "has a claim or complaint, or keeps asking for something out of scope.",
        HandoffArgs,
    ),
)
