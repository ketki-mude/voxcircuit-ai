import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import Admin, Viewer
from app.evaluation.service import EvaluationBusy, EvaluationService, EvaluationUnavailable
from app.models import EvaluationResult, EvaluationRun, TeamUser

router = APIRouter(prefix="/api", tags=["evaluation"])


class EvaluateBody(BaseModel):
    mode: Literal["scripted", "live"] = "scripted"


class RunView(BaseModel):
    id: uuid.UUID
    mode: str
    status: str
    requested_by: str | None
    scenario_count: int
    passed: int
    task_success_rate: float | None
    handoff_accuracy: float | None
    unauthorized_writes: int | None
    history_leaks: int | None
    policy_flags: int | None
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, run: EvaluationRun, requested_by: str | None) -> "RunView":
        return cls(
            id=run.id,
            mode=run.mode,
            status=run.status,
            requested_by=requested_by,
            scenario_count=run.scenario_count,
            passed=run.passed,
            task_success_rate=run.task_success_rate,
            handoff_accuracy=run.handoff_accuracy,
            unauthorized_writes=run.unauthorized_writes,
            history_leaks=run.history_leaks,
            policy_flags=run.policy_flags,
            latency_p50_ms=run.latency_p50_ms,
            latency_p95_ms=run.latency_p95_ms,
            error=run.error,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class ResultView(BaseModel):
    scenario_id: str
    title: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any]
    failures: list[str]
    call_id: uuid.UUID | None
    duration_ms: int


_RUNS = select(EvaluationRun, TeamUser.name).outerjoin(
    TeamUser, TeamUser.id == EvaluationRun.requested_by
)


@router.post("/evaluate", status_code=status.HTTP_202_ACCEPTED)
async def evaluate(body: EvaluateBody, request: Request, user: Admin) -> dict[str, uuid.UUID]:
    """Start an evaluation run in the background. Poll GET /api/evaluation/runs/{id}."""
    service: EvaluationService = request.app.state.evaluation
    try:
        run_id = await service.start(body.mode, requested_by=user.id)
    except EvaluationBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "An evaluation is already running") from exc
    except EvaluationUnavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"run_id": run_id}


@router.get("/evaluation/runs")
async def list_runs(request: Request, _: Viewer) -> list[RunView]:
    async with request.app.state.sessionmaker() as db:
        rows = (await db.execute(_RUNS.order_by(EvaluationRun.started_at.desc()).limit(20))).all()
    return [RunView.of(run, name) for run, name in rows]


@router.get("/evaluation/runs/{run_id}")
async def get_run(run_id: uuid.UUID, request: Request, _: Viewer) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as db:
        row = (await db.execute(_RUNS.where(EvaluationRun.id == run_id))).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
        results = await db.scalars(
            select(EvaluationResult)
            .where(EvaluationResult.run_id == run_id)
            .order_by(EvaluationResult.id)
        )
        return {
            "run": RunView.of(*row),
            "results": [
                ResultView(
                    scenario_id=r.scenario_id,
                    title=r.title,
                    passed=r.passed,
                    expected=r.expected,
                    actual=r.actual,
                    failures=r.failures,
                    call_id=r.call_id,
                    duration_ms=r.duration_ms,
                )
                for r in results
            ],
        }
