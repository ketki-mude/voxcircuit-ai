"""The evaluation: scenarios.yaml, the runner, the metrics, and the API."""

import time
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calls.recorder import CallRecorder
from app.evaluation.checks import (
    Actual,
    count_history_leaks,
    count_unauthorized_writes,
    failures_for,
    percentile,
)
from app.evaluation.runner import ScenarioRunner
from app.evaluation.schema import Expect, ScenarioFile, load_scenarios
from app.evaluation.service import EvaluationService
from app.integrations.mock import MockCalendar, MockCrm
from app.live.fake import scripted_reply
from app.models import Call, EvaluationResult, EvaluationRun
from tests.conftest import AppFactory, FakeSessions, make_settings, team_headers

Sessions = async_sessionmaker[AsyncSession]

# The cases the spec asks scenarios.yaml to cover.
REQUIRED = {
    "book-happy-path",
    "price-request-declined",
    "coverage-advice-declined",
    "angry-caller",
    "claim",
    "complaint",
    "cancel-at-approval",
    "double-booking",
    "crm-saved-calendar-fails",
    "calendar-held-crm-fails",
    "both-systems-fail",
    "correction-twice",
    "returning-customer-verified",
    "returning-customer-fails-verification",
    "other-persons-history",
}


def test_scenario_file() -> None:
    scenarios = load_scenarios()
    assert len(scenarios.scenarios) == 30
    assert 3 <= len(scenarios.live_scenarios) <= 6
    assert {s.id for s in scenarios.scenarios} >= REQUIRED
    assert all(s.model for s in scenarios.scenarios)  # Scripted ones script Maya.
    assert not any(s.model for s in scenarios.live_scenarios)  # Live ones use the model.


async def test_all_scripted_scenarios_pass(sessionmaker: Sessions) -> None:
    settings = make_settings()
    service = EvaluationService(
        settings=settings,
        sessionmaker=sessionmaker,
        live_factory=None,
        runner=ScenarioRunner(settings=settings, sessionmaker=sessionmaker, quiet_s=0.1),
    )
    run_id = await service.start("scripted", requested_by=None)
    await service.wait()

    async with sessionmaker() as db:
        run = await db.get(EvaluationRun, run_id)
        results = list(
            await db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id))
        )
        purposes = set(
            await db.scalars(select(Call.purpose).where(Call.id.in_([r.call_id for r in results])))
        )
    failed = {r.scenario_id: r.failures for r in results if not r.passed}
    assert failed == {}
    assert run is not None and run.status == "completed"
    assert (run.scenario_count, run.passed) == (30, 30)
    assert (run.task_success_rate, run.handoff_accuracy) == (1.0, 1.0)
    assert (run.unauthorized_writes, run.history_leaks) == (0, 0)
    assert run.policy_flags == 4  # The four scenarios where Maya breaks a rule on purpose.
    assert run.latency_p50_ms is not None and run.latency_p95_ms is not None
    assert purposes == {"evaluation"}


async def test_unauthorized_writes_are_detected(sessionmaker: Sessions) -> None:
    async with sessionmaker() as db, db.begin():
        call = Call(engine="fake", input_mode="text", purpose="evaluation")
        db.add(call)
    calendar, crm = MockCalendar(), MockCrm()
    assert await count_unauthorized_writes(sessionmaker, [call.id], calendar, crm) == 0
    calendar.write_keys.append(f"{uuid.uuid4()}:calendar_event")  # No approval behind it.
    crm.write_keys.append("made-up-key")
    assert await count_unauthorized_writes(sessionmaker, [call.id], calendar, crm) == 2


async def test_history_leaks_are_detected(sessionmaker: Sessions) -> None:
    async with sessionmaker() as db, db.begin():
        call = Call(engine="fake", input_mode="text", purpose="evaluation")
        db.add(call)
    recorder = CallRecorder(sessionmaker)
    # History returned with no verification at all, mentioning another customer's car.
    await recorder.record_tool_call(
        call.id,
        provider_call_id="x",
        name="get_previous_interactions",
        args={},
        result={"first_name": "Lee", "vehicle": "2015 Volvo"},
        ok=True,
        duration_ms=1,
    )
    assert await count_history_leaks(sessionmaker, call.id, []) == 1
    assert await count_history_leaks(sessionmaker, call.id, ["Volvo"]) == 2


def test_failures_name_each_mismatch() -> None:
    actual = Actual(
        booking="none",
        handoff="none",
        policy=["price"],
        policy_events=1,
        verified=False,
        tool_errors=0,
        calendar_events=0,
        replies=["Hello."],
        latencies_ms=[],
        unauthorized_writes=1,
    )
    expect = Expect(booking="booked", handoff="none", policy=[], reply_contains=["AI assistant"])
    assert failures_for(expect, actual) == [
        "booking: expected booked, got none",
        "policy: expected [], got ['price']",
        "no reply contains 'AI assistant'",
        "1 unauthorized write(s)",
    ]


def test_percentile_matches_postgres() -> None:
    assert percentile([], 0.5) is None
    assert percentile([100], 0.95) == 100
    assert percentile([100, 200, 300, 400], 0.5) == 250
    assert percentile([100, 200, 300, 400], 0.95) == 385


def _small(file: ScenarioFile, count: int = 2) -> ScenarioFile:
    return file.model_copy(update={"scenarios": file.scenarios[:count]})


def _wait_for_run(client: TestClient, headers: dict[str, str], run_id: str) -> dict:  # type: ignore[type-arg]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = client.get(f"/api/evaluation/runs/{run_id}", headers=headers).json()
        if body["run"]["status"] != "running":
            return body  # type: ignore[no-any-return]
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_evaluation_api(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client()
    state = client.app.state  # type: ignore[attr-defined]
    state.evaluation = EvaluationService(
        settings=state.settings,
        sessionmaker=state.sessionmaker,
        live_factory=None,
        load=lambda: _small(load_scenarios()),
        runner=ScenarioRunner(
            settings=state.settings, sessionmaker=state.sessionmaker, quiet_s=0.1
        ),
    )
    admin = team_headers(client, db_engine, "admin")
    viewer = team_headers(client, db_engine, "viewer")

    assert (
        client.post("/api/evaluate", json={"mode": "scripted"}, headers=viewer).status_code == 403
    )
    started = client.post("/api/evaluate", json={"mode": "scripted"}, headers=admin)
    assert started.status_code == 202
    busy = client.post("/api/evaluate", json={"mode": "scripted"}, headers=admin)
    assert busy.status_code == 409

    body = _wait_for_run(client, viewer, started.json()["run_id"])
    assert body["run"]["status"] == "completed"
    assert body["run"]["requested_by"] == "Admin Tester"
    assert [r["passed"] for r in body["results"]] == [True, True]
    assert body["results"][0]["actual"]["booking"] == "booked"
    runs = client.get("/api/evaluation/runs", headers=viewer).json()
    assert runs[0]["id"] == started.json()["run_id"]

    # Live mode needs a key, and enough of today's call cap.
    live = client.post("/api/evaluate", json={"mode": "live"}, headers=admin)
    assert (live.status_code, live.json()["detail"]) == (
        400,
        "Live evaluation needs GEMINI_API_KEY.",
    )


def test_live_mode_respects_the_daily_cap(make_client: AppFactory, db_engine: Engine) -> None:
    client = make_client(gemini_api_key="test-key", daily_call_cap=1)
    admin = team_headers(client, db_engine, "admin")
    response = client.post("/api/evaluate", json={"mode": "live"}, headers=admin)
    assert response.status_code == 400
    assert "under today's call cap" in response.json()["detail"]


def test_live_mode_runs_calls_against_the_model(make_client: AppFactory, db_engine: Engine) -> None:
    # A stand-in for Gemini: the plumbing is what is tested here, not the model.
    sessions = FakeSessions(
        [scripted_reply("Hi, I'm Maya, an AI assistant for Northstar Insurance.")]
    )
    client = make_client(sessions=sessions, gemini_api_key="test-key")
    state = client.app.state  # type: ignore[attr-defined]
    state.evaluation = EvaluationService(
        settings=state.settings,
        sessionmaker=state.sessionmaker,
        live_factory=sessions,
        runner=ScenarioRunner(
            settings=state.settings,
            sessionmaker=state.sessionmaker,
            live_factory=sessions,
            live_quiet_s=0.1,
            live_turn_timeout_s=5,
        ),
    )
    admin = team_headers(client, db_engine, "admin")

    started = client.post("/api/evaluate", json={"mode": "live"}, headers=admin)
    body = _wait_for_run(client, admin, started.json()["run_id"])

    assert body["run"]["status"] == "completed"
    assert body["run"]["mode"] == "live"
    assert len(body["results"]) == len(load_scenarios().live_scenarios)
    disclosure = next(r for r in body["results"] if r["scenario_id"] == "live-disclosure")
    assert disclosure["passed"] is True
    with db_engine.connect() as conn:
        engines = set(
            conn.scalars(
                select(Call.engine).where(Call.id.in_([r["call_id"] for r in body["results"]]))
            )
        )
    assert engines == {"gemini"}  # Live evaluation calls count against the cap.
