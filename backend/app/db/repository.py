"""Repository layer for database CRUD operations and statistical aggregations."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..data_collection.schemas import PipelineRunRecord
from .models import PipelineRunModel


def upsert_pipeline_run(
    db: Session,
    record: PipelineRunRecord,
) -> Tuple[PipelineRunModel, bool]:
    """Insert or update a pipeline run to prevent duplicate records.

    Args:
        db: Active database session.
        record: PipelineRunRecord to persist.

    Returns:
        Tuple of (model_instance, was_created_boolean).
    """
    existing = db.query(PipelineRunModel).filter(
        PipelineRunModel.run_id == record.run_id
    ).first()

    if existing:
        existing.repository = record.repository
        existing.branch = record.branch
        existing.commit_sha = record.commit_sha
        existing.status = record.status
        existing.conclusion = record.conclusion
        existing.workflow_name = record.workflow_name
        existing.event = record.event
        existing.duration_seconds = record.duration_seconds
        existing.failed_stage = record.failed_stage
        existing.failure_type = record.failure_type
        existing.created_at = record.created_at
        existing.synced_at = datetime.now(timezone.utc)
        return existing, False

    new_run = PipelineRunModel.from_pydantic(record)
    db.add(new_run)
    return new_run, True


def upsert_pipeline_runs(
    db: Session,
    records: List[PipelineRunRecord],
) -> Dict[str, int]:
    """Persist a batch of PipelineRunRecord instances, preventing duplicates.

    Args:
        db: Active database session.
        records: List of pipeline run records.

    Returns:
        Dict with count of inserted, updated, and total processed records.
    """
    inserted = 0
    updated = 0

    for record in records:
        _, is_created = upsert_pipeline_run(db, record)
        if is_created:
            inserted += 1
        else:
            updated += 1

    db.commit()
    return {
        "total": len(records),
        "inserted": inserted,
        "updated": updated,
    }


def get_pipeline_runs(
    db: Session,
    limit: int = 50,
    offset: int = 0,
    repository: Optional[str] = None,
    branch: Optional[str] = None,
    status: Optional[str] = None,
    conclusion: Optional[str] = None,
    workflow_name: Optional[str] = None,
    failure_type: Optional[str] = None,
) -> List[PipelineRunModel]:
    """Query stored pipeline runs with filtering and pagination.

    Args:
        db: Active database session.
        limit: Max records to return.
        offset: Record offset for pagination.
        repository: Filter by repository name.
        branch: Filter by Git branch.
        status: Filter by run status.
        conclusion: Filter by run conclusion.
        workflow_name: Filter by workflow name.
        failure_type: Filter by failure category.

    Returns:
        List of matching PipelineRunModel instances.
    """
    query = db.query(PipelineRunModel)

    if repository:
        query = query.filter(PipelineRunModel.repository == repository)
    if branch:
        query = query.filter(PipelineRunModel.branch == branch)
    if status:
        query = query.filter(PipelineRunModel.status == status)
    if conclusion:
        query = query.filter(PipelineRunModel.conclusion == conclusion)
    if workflow_name:
        query = query.filter(PipelineRunModel.workflow_name == workflow_name)
    if failure_type:
        query = query.filter(PipelineRunModel.failure_type == failure_type)

    return (
        query.order_by(PipelineRunModel.created_at.desc())
        .offset(offset)
        .limit(min(max(1, limit), 100))
        .all()
    )


def get_pipeline_run_by_id(
    db: Session,
    run_id: int,
) -> Optional[PipelineRunModel]:
    """Retrieve a single pipeline run by its unique GitHub Actions run ID."""
    return (
        db.query(PipelineRunModel)
        .filter(PipelineRunModel.run_id == run_id)
        .first()
    )


def get_pipeline_stats(
    db: Session,
    repository: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate aggregated pipeline execution metrics.

    Args:
        db: Active database session.
        repository: Optional filter by repository slug.

    Returns:
        Dictionary containing counts, success rate, duration averages, and distributions.
    """
    query = db.query(PipelineRunModel)
    if repository:
        query = query.filter(PipelineRunModel.repository == repository)

    runs = query.all()
    total_runs = len(runs)

    if total_runs == 0:
        return {
            "total_runs": 0,
            "success_count": 0,
            "failure_count": 0,
            "other_count": 0,
            "success_rate_percent": 0.0,
            "failure_rate_percent": 0.0,
            "average_duration_seconds": None,
            "conclusions": {},
            "failure_types": {},
            "failed_stages": {},
        }

    success_count = sum(1 for r in runs if r.conclusion == "success")
    failure_count = sum(1 for r in runs if r.conclusion == "failure")
    other_count = total_runs - (success_count + failure_count)

    success_rate = round((success_count / total_runs) * 100, 2)
    failure_rate = round((failure_count / total_runs) * 100, 2)

    durations = [r.duration_seconds for r in runs if r.duration_seconds is not None]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else None

    conclusions: Dict[str, int] = {}
    failure_types: Dict[str, int] = {}
    failed_stages: Dict[str, int] = {}

    for r in runs:
        if r.conclusion:
            conclusions[r.conclusion] = conclusions.get(r.conclusion, 0) + 1
        if r.failure_type:
            failure_types[r.failure_type] = failure_types.get(r.failure_type, 0) + 1
        if r.failed_stage:
            failed_stages[r.failed_stage] = failed_stages.get(r.failed_stage, 0) + 1

    return {
        "total_runs": total_runs,
        "success_count": success_count,
        "failure_count": failure_count,
        "other_count": other_count,
        "success_rate_percent": success_rate,
        "failure_rate_percent": failure_rate,
        "average_duration_seconds": avg_duration,
        "conclusions": conclusions,
        "failure_types": failure_types,
        "failed_stages": failed_stages,
    }
