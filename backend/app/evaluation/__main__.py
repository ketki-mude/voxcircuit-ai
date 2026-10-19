"""Run the evaluation from the command line and print the results.

    python -m app.evaluation               # the 30 scripted scenarios, no network
    python -m app.evaluation --mode live   # about 5 scenarios against Gemini Live

Uses DATABASE_URL. Exits with status 1 if any scenario fails.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db import create_db_engine, create_sessionmaker
from app.evaluation.service import EvaluationService
from app.live.factory import default_live_session_factory
from app.models import EvaluationResult, EvaluationRun


async def main(mode: str) -> int:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    service = EvaluationService(
        settings=settings,
        sessionmaker=sessionmaker,
        live_factory=default_live_session_factory(settings),
    )
    try:
        run_id: uuid.UUID = await service.start(
            "live" if mode == "live" else "scripted", requested_by=None
        )
        await service.wait()
        async with sessionmaker() as db:
            run = await db.get(EvaluationRun, run_id)
            results = list(
                await db.scalars(
                    select(EvaluationResult)
                    .where(EvaluationResult.run_id == run_id)
                    .order_by(EvaluationResult.id)
                )
            )
    finally:
        await engine.dispose()
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        detail = "" if result.passed else "  " + "; ".join(result.failures)
        print(f"{mark}  {result.scenario_id}{detail}")
    if run is None:
        return 1
    print(
        f"\n{run.passed}/{run.scenario_count} passed"
        f"  task success {_pct(run.task_success_rate)}"
        f"  handoff accuracy {_pct(run.handoff_accuracy)}"
        f"  unauthorized writes {run.unauthorized_writes}"
        f"  history leaks {run.history_leaks}"
        f"  policy flags {run.policy_flags}"
        f"  latency p50/p95 {run.latency_p50_ms}/{run.latency_p95_ms} ms"
    )
    if run.error:
        print(f"Run failed: {run.error}")
    return 0 if run.status == "completed" and run.passed == run.scenario_count else 1


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:.0%}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.evaluation")
    parser.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    raise SystemExit(asyncio.run(main(parser.parse_args().mode)))
