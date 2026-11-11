"""Database adapter converting stored pipeline run rows into optimization models."""

from typing import List, Optional
from sqlalchemy.orm import Session

from backend.app.db.models import PipelineRunModel
from backend.app.db.repository import get_pipeline_runs
from .models import PipelineRun

MAX_ANALYSIS_WINDOW = 100
ELIGIBLE_CONCLUSIONS = ("success", "failure")


def _to_optimization_run(row: PipelineRunModel) -> Optional[PipelineRun]:
    """Map an eligible ORM run record into an optimization PipelineRun without fabricating stages."""
    if (
        row.status != "completed"
        or row.conclusion not in ELIGIBLE_CONCLUSIONS
        or row.duration_seconds is None
    ):
        return None

    return PipelineRun(
        run_id=str(row.run_id),
        status=row.conclusion,
        pipeline_duration=float(row.duration_seconds),
        timestamp=row.created_at,
        stages=[],
        failed_stage=row.failed_stage,
    )


def get_pipeline_data(
    db: Session,
    repository: Optional[str] = None,
    limit: int = MAX_ANALYSIS_WINDOW,
) -> List[PipelineRun]:
    """Retrieve eligible completed pipeline executions from PostgreSQL for optimization analysis.

    Uses Member 1's existing repository and session dependency without creating a second DB layer.
    Caps the query window at 100 recent runs and omits incomplete, cancelled, skipped, or
    duration-less executions. Stage lists remain empty when granular stage durations are absent.
    """
    window_limit = min(max(1, limit), MAX_ANALYSIS_WINDOW)
    rows = get_pipeline_runs(
        db=db,
        limit=window_limit,
        repository=repository,
        status="completed",
    )

    runs: List[PipelineRun] = []
    for row in rows:
        mapped = _to_optimization_run(row)
        if mapped is not None:
            runs.append(mapped)
    return runs
