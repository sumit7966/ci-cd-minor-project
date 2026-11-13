"""Database adapter converting stored pipeline run, job, and step rows into optimization models."""

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.db.models import PipelineJobModel, PipelineRunModel, PipelineStepModel
from backend.app.db.repository import get_pipeline_runs
from .models import PipelineRun, StageRun

MAX_ANALYSIS_WINDOW = 100
ELIGIBLE_CONCLUSIONS = ("success", "failure")
_SKIPPED_VALUES = frozenset({"skipped"})
_INCOMPLETE_STATUSES = frozenset({"queued", "waiting", "pending", "in_progress"})
_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out"})


def _is_valid_non_negative_duration(value: Optional[float]) -> bool:
    """Return True when duration is a finite non-negative number."""
    return value is not None and math.isfinite(value) and value >= 0


def _normalize_interval(
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
) -> Optional[Tuple[datetime, datetime, bool]]:
    """Validate and normalize a (started_at, completed_at) pair.

    Returns (start, end, is_tz_aware) or None when timestamps are missing/invalid.
    """
    if started_at is None or completed_at is None:
        return None
    start_aware = started_at.tzinfo is not None
    end_aware = completed_at.tzinfo is not None
    if start_aware != end_aware:
        return None
    if start_aware:
        start_norm = started_at.astimezone(timezone.utc)
        end_norm = completed_at.astimezone(timezone.utc)
    else:
        start_norm = started_at
        end_norm = completed_at
    if end_norm < start_norm:
        return None
    return start_norm, end_norm, start_aware


def _compute_interval_union_seconds(steps: List[PipelineStepModel]) -> Optional[float]:
    """Calculate the union duration (in seconds) of step timestamp intervals across parallel jobs.

    Returns None if any executed step lacks valid, timezone-consistent timestamps.
    """
    raw_intervals: List[Tuple[datetime, datetime, bool]] = []
    for step in steps:
        normalized = _normalize_interval(step.started_at, step.completed_at)
        if normalized is None:
            return None
        raw_intervals.append(normalized)

    if not raw_intervals:
        return None

    # All intervals in a run must share either naive or UTC-aware comparability.
    awareness_flags = {item[2] for item in raw_intervals}
    if len(awareness_flags) > 1:
        return None

    sorted_intervals = sorted((start, end) for start, end, _ in raw_intervals)
    merged: List[List[datetime]] = []
    for start, end in sorted_intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            if end > merged[-1][1]:
                merged[-1][1] = end

    total_seconds = sum((end - start).total_seconds() for start, end in merged)
    return round(max(0.0, total_seconds), 2)


def _classify_step(step: PipelineStepModel) -> str:
    """Classify a pipeline step as 'skipped', 'incomplete', or 'executed'."""
    status_val = (step.status or "").strip().lower()
    conclusion_val = (step.conclusion or "").strip().lower()

    if conclusion_val in _SKIPPED_VALUES or status_val in _SKIPPED_VALUES:
        return "skipped"
    if status_val in _INCOMPLETE_STATUSES or status_val != "completed":
        return "incomplete"
    return "executed"


def _compute_run_queue_duration(jobs: List[PipelineJobModel]) -> Optional[float]:
    """Compute pipeline queue wait duration from non-skipped jobs without fabricating missing timestamps."""
    valid_queues: List[float] = []
    for job in jobs:
        if (job.conclusion or "").strip().lower() in _SKIPPED_VALUES:
            continue
        if _is_valid_non_negative_duration(job.queue_duration_seconds):
            valid_queues.append(float(job.queue_duration_seconds))  # type: ignore[arg-type]
        else:
            interval = _normalize_interval(job.created_at, job.started_at)
            if interval is not None:
                start_dt, end_dt, _ = interval
                valid_queues.append((end_dt - start_dt).total_seconds())

    if not valid_queues:
        return None
    return round(max(0.0, min(valid_queues)), 2)


def _aggregate_stage_run(
    *,
    stage_name: str,
    executed_by_job: Dict[int, List[PipelineStepModel]],
    skipped_count: int,
    incomplete_count: int,
    run_failed_stage: Optional[str],
    run_failure_type: Optional[str],
) -> StageRun:
    """Aggregate all steps belonging to one normalized stage within a pipeline run."""
    executed_steps = [step for job_steps in executed_by_job.values() for step in job_steps]

    if not executed_steps:
        status_val = "skipped" if (skipped_count > 0 and incomplete_count == 0) else "incomplete"
        return StageRun(
            name=stage_name,
            duration=0.0,
            status=status_val,
            failure_type=None,
            duration_available=False,
            skipped_count=skipped_count,
            incomplete_count=incomplete_count,
        )

    failed_steps = [
        step
        for step in executed_steps
        if (step.conclusion or "").strip().lower() in _FAILED_CONCLUSIONS
    ]

    all_durations_valid = all(
        _is_valid_non_negative_duration(step.duration_seconds) for step in executed_steps
    )

    if not all_durations_valid:
        stage_duration = 0.0
        duration_available = False
    elif len(executed_by_job) == 1:
        # Sequential steps within a single job sum cleanly.
        single_job_steps = next(iter(executed_by_job.values()))
        stage_duration = round(
            max(0.0, sum(float(step.duration_seconds) for step in single_job_steps)),  # type: ignore[arg-type]
            2,
        )
        duration_available = True
    else:
        # Parallel jobs: compute timestamp interval union so overlapping work is not double-counted.
        union_duration = _compute_interval_union_seconds(executed_steps)
        if union_duration is not None:
            stage_duration = union_duration
        else:
            # Fallback when timestamps are unavailable/invalid across parallel jobs:
            # use max(per-job sequential sum) as the concurrent bound rather than summing sequential work.
            per_job_sums = [
                sum(float(step.duration_seconds) for step in job_steps)  # type: ignore[arg-type]
                for job_steps in executed_by_job.values()
            ]
            stage_duration = round(max(0.0, max(per_job_sums)), 2)
        duration_available = True

    if failed_steps:
        stage_status = "failure"
    elif incomplete_count > 0:
        stage_status = "incomplete"
        duration_available = False
    else:
        stage_status = "success"

    failure_type: Optional[str] = None
    if stage_status == "failure":
        if run_failed_stage == stage_name and run_failure_type and run_failure_type != "none":
            failure_type = run_failure_type
        elif any((step.conclusion or "").strip().lower() == "timed_out" for step in failed_steps):
            failure_type = "timeout"

    return StageRun(
        name=stage_name,
        duration=stage_duration,
        status=stage_status,
        failure_type=failure_type,
        duration_available=duration_available,
        skipped_count=skipped_count,
        incomplete_count=incomplete_count,
    )


def _aggregate_stages_from_jobs(
    row: PipelineRunModel,
) -> Tuple[List[StageRun], int, int, Optional[float]]:
    """Aggregate nested job and step ORM rows into ordered StageRun telemetry and run metadata."""
    jobs = list(getattr(row, "jobs", None) or [])
    job_count = len(jobs)
    step_count = sum(len(getattr(job, "steps", None) or []) for job in jobs)
    queue_duration_seconds = _compute_run_queue_duration(jobs)

    if not jobs:
        return [], 0, 0, None

    stage_order: List[str] = []
    executed_by_stage_and_job: Dict[str, Dict[int, List[PipelineStepModel]]] = defaultdict(
        lambda: defaultdict(list)
    )
    skipped_by_stage: Dict[str, int] = defaultdict(int)
    incomplete_by_stage: Dict[str, int] = defaultdict(int)

    for job_idx, job in enumerate(jobs):
        for step in (getattr(job, "steps", None) or []):
            stage_name = (step.normalized_stage or "").strip()
            if not stage_name:
                # Ignore unnormalized lifecycle hooks (e.g., 'Set up job', 'Complete job').
                continue

            if stage_name not in executed_by_stage_and_job and stage_name not in skipped_by_stage and stage_name not in incomplete_by_stage:
                stage_order.append(stage_name)

            classification = _classify_step(step)
            if classification == "skipped":
                skipped_by_stage[stage_name] += 1
            elif classification == "incomplete":
                incomplete_by_stage[stage_name] += 1
            else:
                executed_by_stage_and_job[stage_name][job_idx].append(step)

    stages = [
        _aggregate_stage_run(
            stage_name=stage_name,
            executed_by_job=executed_by_stage_and_job.get(stage_name, {}),
            skipped_count=skipped_by_stage.get(stage_name, 0),
            incomplete_count=incomplete_by_stage.get(stage_name, 0),
            run_failed_stage=row.failed_stage,
            run_failure_type=row.failure_type,
        )
        for stage_name in stage_order
    ]
    return stages, job_count, step_count, queue_duration_seconds


def _to_optimization_run(row: PipelineRunModel) -> Optional[PipelineRun]:
    """Map an eligible ORM run record (and any stored job/step telemetry) into PipelineRun."""
    if (
        row.status != "completed"
        or row.conclusion not in ELIGIBLE_CONCLUSIONS
        or row.duration_seconds is None
        or not math.isfinite(row.duration_seconds)
        or row.duration_seconds < 0
    ):
        return None

    stages, job_count, step_count, queue_duration_seconds = _aggregate_stages_from_jobs(row)

    return PipelineRun(
        run_id=str(row.run_id),
        status=row.conclusion,
        pipeline_duration=float(row.duration_seconds),
        timestamp=row.created_at,
        stages=stages,
        failed_stage=row.failed_stage,
        has_stage_telemetry=bool(stages),
        job_count=job_count,
        step_count=step_count,
        queue_duration_seconds=queue_duration_seconds,
    )


def get_pipeline_data(
    db: Session,
    repository: Optional[str] = None,
    limit: int = MAX_ANALYSIS_WINDOW,
) -> List[PipelineRun]:
    """Retrieve eligible completed pipeline executions and eager-loaded telemetry from PostgreSQL.

    Uses the repository's selectinload path to retrieve runs, jobs, and steps in bounded queries
    without N+1 overhead. Omits incomplete, cancelled, skipped, or duration-less runs, and
    preserves empty stages when child job/step telemetry has not been collected.
    """
    window_limit = min(max(1, limit), MAX_ANALYSIS_WINDOW)
    rows = get_pipeline_runs(
        db=db,
        limit=window_limit,
        repository=repository,
        status="completed",
        conclusions=ELIGIBLE_CONCLUSIONS,
        include_telemetry=True,
    )

    runs: List[PipelineRun] = []
    for row in rows:
        mapped = _to_optimization_run(row)
        if mapped is not None:
            runs.append(mapped)
    return runs
