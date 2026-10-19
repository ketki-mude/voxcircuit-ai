"""Evaluation runs: start one in the background, store each scenario's result and the
run's metrics."""

import asyncio
import logging
import uuid
from collections.abc import Callable

from sqlalchemy import func, not_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calls.recorder import OFFLINE_EVALUATION, start_of_today
from app.config import Settings
from app.evaluation.checks import RunMetrics
from app.evaluation.runner import Mode, ScenarioRunner
from app.evaluation.schema import Scenario, ScenarioFile, load_scenarios
from app.live.factory import LiveSessionFactory
from app.models import AuditLog, Call, EvaluationResult, EvaluationRun

log = logging.getLogger(__name__)


class EvaluationBusy(Exception):
    """A run is already in progress."""


class EvaluationUnavailable(Exception):
    """This mode cannot run right now. The message says why."""


class EvaluationService:
    def __init__(
        self,
        *,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        live_factory: LiveSessionFactory | None,
        load: Callable[[], ScenarioFile] = load_scenarios,
        runner: ScenarioRunner | None = None,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._load = load
        self._runner = runner or ScenarioRunner(
            settings=settings, sessionmaker=sessionmaker, live_factory=live_factory
        )
        self._task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, mode: Mode, *, requested_by: uuid.UUID | None) -> uuid.UUID:
        if self.busy:
            raise EvaluationBusy
        file = self._load()
        scenarios = file.scenarios if mode == "scripted" else file.live_scenarios
        if mode == "live":
            await self._check_live_budget(len(scenarios))
        async with self._sessionmaker() as db, db.begin():
            run = EvaluationRun(mode=mode, requested_by=requested_by, scenario_count=len(scenarios))
            db.add(run)
            await db.flush()
            db.add(
                AuditLog(
                    actor="team",
                    event="evaluation.started",
                    payload={"run_id": str(run.id), "mode": mode},
                )
            )
        self._task = asyncio.create_task(self.run(run.id, mode, scenarios))
        return run.id

    async def run(self, run_id: uuid.UUID, mode: Mode, scenarios: list[Scenario]) -> None:
        metrics = RunMetrics()
        try:
            for scenario in scenarios:
                outcome = await self._runner.run(scenario, mode)
                if outcome.actual is not None:
                    metrics.add(scenario.expect, outcome.actual, outcome.passed)
                else:
                    metrics.scenario_count += 1
                async with self._sessionmaker() as db, db.begin():
                    db.add(
                        EvaluationResult(
                            run_id=run_id,
                            scenario_id=scenario.id,
                            title=scenario.title,
                            passed=outcome.passed,
                            expected=scenario.expect.model_dump(exclude_none=True),
                            actual=outcome.actual.summary() if outcome.actual else {},
                            failures=outcome.failures,
                            call_id=outcome.call_id,
                            duration_ms=outcome.duration_ms,
                        )
                    )
            await self._finish(run_id, status="completed", **metrics.as_columns())
        except Exception as exc:
            log.exception("Evaluation run %s failed", run_id)
            await self._finish(run_id, status="failed", error=str(exc)[:500])

    async def _finish(self, run_id: uuid.UUID, **values: object) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(finished_at=func.now(), **values)
            )

    async def _check_live_budget(self, needed: int) -> None:
        if self._settings.gemini_key is None:
            raise EvaluationUnavailable("Live evaluation needs GEMINI_API_KEY.")
        async with self._sessionmaker() as db:
            used = await db.scalar(
                select(func.count())
                .select_from(Call)
                .where(Call.started_at >= start_of_today(), not_(OFFLINE_EVALUATION))
            )
        left = self._settings.daily_call_cap - int(used or 0)
        if left < needed:
            raise EvaluationUnavailable(
                f"Live evaluation needs {needed} calls, but only {max(left, 0)} are left "
                "under today's call cap."
            )

    async def mark_interrupted(self) -> None:
        """At startup: runs still marked running were cut off by a restart."""
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                update(EvaluationRun)
                .where(EvaluationRun.status == "running")
                .values(status="failed", error="Interrupted by a restart", finished_at=func.now())
            )

    async def wait(self) -> None:
        """Wait for the current run, if any, to finish."""
        if self._task is not None:
            await asyncio.wait([self._task])

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.wait([self._task])
