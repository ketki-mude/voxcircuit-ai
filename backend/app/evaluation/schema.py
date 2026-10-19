"""The format of scenarios.yaml, validated when it is loaded.

A scenario scripts Maya's side (model turns, played by FakeLiveSession) and the caller's
side (what they say and click), then states what must be true afterwards. In live mode
the model turns are ignored: the real model answers.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.integrations.base import FaultMode

DEFAULT_PATH = Path(__file__).with_name("scenarios.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SayTurn(_Strict):
    """Maya says this."""

    say: str


class ToolTurn(_Strict):
    """Maya calls this tool. Arguments may use {placeholders} and $first_slot,
    $second_slot or $lead_id, which are filled in from earlier tool results."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


ModelTurn = SayTurn | ToolTurn


class CallerSays(_Strict):
    say: str


class CallerAction(_Strict):
    action: Literal["approve", "approve_if_ticket", "cancel", "competitor_takes_slot", "end"]


class CallerEntersCode(_Strict):
    enter_code: Literal["correct", "wrong"]


CallerStep = CallerSays | CallerAction | CallerEntersCode


class Setup(_Strict):
    crm: FaultMode = "ok"
    calendar: FaultMode = "ok"
    returning_customer: bool = False  # Seeds "Pat" with a booked appointment: {returning_email}
    other_customer: bool = False  # Seeds "Lee", whose history must never leak: {other_email}


class Expect(_Strict):
    """What must be true after the call. Fields left out are not checked."""

    booking: Literal["booked", "rescheduled", "not_booked", "none"] | None = None
    handoff: str | None = None  # A handoff reason, or "none".
    policy: list[str] | None = None  # The policy rules flagged, in any order.
    verified: bool | None = None
    tool_errors: int | None = None
    calendar_events: int | None = None
    reply_contains: list[str] | None = None  # Each phrase appears in some reply.


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    setup: Setup = Setup()
    model: list[ModelTurn] = Field(default_factory=list)
    caller: list[CallerStep]
    expect: Expect


class ScenarioFile(_Strict):
    version: Literal[1]
    defs: dict[str, Any] = Field(default_factory=dict)  # YAML anchors live here.
    scenarios: list[Scenario]
    live_scenarios: list[Scenario]


def load_scenarios(path: Path = DEFAULT_PATH) -> ScenarioFile:
    data = yaml.safe_load(path.read_text())
    loaded = ScenarioFile.model_validate(data)
    ids = [s.id for s in [*loaded.scenarios, *loaded.live_scenarios]]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"Duplicate scenario ids: {sorted(duplicates)}")
    return loaded
