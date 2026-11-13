"""Repository layer for database CRUD operations and statistical aggregations."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, selectinload

from ..data_collection.schemas import PipelineJobRecord, PipelineRunRecord
from .models import (
    PipelineJobModel,
    PipelineRunModel,
    PipelineStepModel,
    build_job_key,
)


def _resolve_job_sync_policy(
    record: PipelineRunRecord,
    replace_jobs: Optional[bool],
) -> Tuple[bool, bool]:
    """Determine (should_sync_jobs, prune_missing_children) safely."""
    jobs_complete = getattr(record, "_jobs_complete", None)
    if replace_jobs is True or jobs_complete is True:
        return True, True
    if replace_jobs is False or jobs_complete is False:
        return False, False
    if not record.jobs:
        # Legacy or partial callers supplying jobs=[] must not erase existing telemetry.
        return False, False
    return True, False


def _dedupe_job_records(jobs: List[PipelineJobRecord]) -> Dict[str, PipelineJobRecord]:
    """Deduplicate incoming job records by deterministic job_key."""
    deduped: Dict[str, PipelineJobRecord] = {}
    for job_rec in jobs:
        job_key = build_job_key(
            job_name=job_rec.job_name,
            github_job_id=job_rec.github_job_id,
            run_attempt=job_rec.run_attempt,
        )
        deduped[job_key] = job_rec
    return deduped


def _load_existing_job_lookups(
    db: Session,
    run_id: int,
) -> Tuple[
    List[PipelineJobModel],
    Dict[str, PipelineJobModel],
    Dict[int, PipelineJobModel],
    Dict[Tuple[str, int], PipelineJobModel],
]:
    """Load existing jobs for a run and build identity lookup indices."""
    existing_jobs = (
        db.query(PipelineJobModel)
        .filter(PipelineJobModel.run_id == run_id)
        .all()
    )
    existing_by_job_key = {job.job_key: job for job in existing_jobs}
    existing_by_github_id = {
        job.github_job_id: job
        for job in existing_jobs
        if job.github_job_id is not None
    }
    legacy_jobs_by_identity = {
        ((job.job_name or "").strip(), job.run_attempt or 1): job
        for job in existing_jobs
        if job.github_job_id is None
    }
    return existing_jobs, existing_by_job_key, existing_by_github_id, legacy_jobs_by_identity


def _match_existing_job(
    job_rec: PipelineJobRecord,
    target_job_key: str,
    existing_by_job_key: Dict[str, PipelineJobModel],
    existing_by_github_id: Dict[int, PipelineJobModel],
    legacy_jobs_by_identity: Dict[Tuple[str, int], PipelineJobModel],
    matched_jobs: Set[PipelineJobModel],
) -> Optional[PipelineJobModel]:
    """Resolve an existing job ORM row using job_key, github_job_id, or legacy identity."""
    existing_job = existing_by_job_key.get(target_job_key)
    if existing_job is None and job_rec.github_job_id is not None:
        existing_job = existing_by_github_id.get(job_rec.github_job_id)
    if existing_job is None and job_rec.github_job_id is not None:
        candidate_legacy = legacy_jobs_by_identity.get(
            (job_rec.job_name.strip(), job_rec.run_attempt or 1)
        )
        if candidate_legacy is not None and candidate_legacy not in matched_jobs:
            existing_job = candidate_legacy
    return existing_job


def _upsert_run_job_children(
    db: Session,
    run_model: PipelineRunModel,
    run_id: int,
    jobs: List[PipelineJobRecord],
    *,
    prune_missing: bool,
    allow_empty_reconcile: bool = False,
) -> Dict[str, int]:
    """Idempotently upsert child jobs and steps for one run without altering pipeline_runs."""
    db.flush()
    deduped_jobs = _dedupe_job_records(jobs)
    if not deduped_jobs and not (prune_missing and allow_empty_reconcile):
        return {
            "jobs_inserted": 0,
            "jobs_updated": 0,
            "steps_inserted": 0,
            "steps_updated": 0,
            "jobs_synced": 0,
            "steps_synced": 0,
        }

    now = datetime.now(timezone.utc)
    (
        existing_jobs,
        existing_by_job_key,
        existing_by_github_id,
        legacy_jobs_by_identity,
    ) = _load_existing_job_lookups(db, run_id)

    kept_jobs: Set[PipelineJobModel] = set()
    jobs_inserted = 0
    jobs_updated = 0
    steps_inserted = 0
    steps_updated = 0

    for target_job_key, job_rec in deduped_jobs.items():
        existing_job = _match_existing_job(
            job_rec,
            target_job_key,
            existing_by_job_key,
            existing_by_github_id,
            legacy_jobs_by_identity,
            kept_jobs,
        )

        if existing_job is not None:
            existing_job.github_job_id = job_rec.github_job_id
            existing_job.job_key = target_job_key
            existing_job.job_name = job_rec.job_name
            existing_job.run_attempt = job_rec.run_attempt
            existing_job.status = job_rec.status
            existing_job.conclusion = job_rec.conclusion
            existing_job.created_at = job_rec.created_at
            existing_job.started_at = job_rec.started_at
            existing_job.completed_at = job_rec.completed_at
            existing_job.queue_duration_seconds = job_rec.queue_duration_seconds
            existing_job.execution_duration_seconds = job_rec.execution_duration_seconds
            existing_job.runner_name = job_rec.runner_name
            existing_job.synced_at = now
            job_model = existing_job
            jobs_updated += 1
        else:
            job_model = PipelineJobModel(
                run_id=run_id,
                github_job_id=job_rec.github_job_id,
                job_key=target_job_key,
                job_name=job_rec.job_name,
                run_attempt=job_rec.run_attempt,
                status=job_rec.status,
                conclusion=job_rec.conclusion,
                created_at=job_rec.created_at,
                started_at=job_rec.started_at,
                completed_at=job_rec.completed_at,
                queue_duration_seconds=job_rec.queue_duration_seconds,
                execution_duration_seconds=job_rec.execution_duration_seconds,
                runner_name=job_rec.runner_name,
                synced_at=now,
            )
            db.add(job_model)
            db.flush()
            jobs_inserted += 1

        kept_jobs.add(job_model)

        deduped_steps = {step.step_number: step for step in job_rec.steps}
        existing_steps = (
            db.query(PipelineStepModel)
            .filter(PipelineStepModel.job_id == job_model.id)
            .all()
            if existing_job is not None
            else []
        )
        existing_steps_by_num = {step.step_number: step for step in existing_steps}

        for step_num, step_rec in deduped_steps.items():
            existing_step = existing_steps_by_num.get(step_num)
            if existing_step is not None:
                existing_step.run_id = run_id
                existing_step.step_name = step_rec.step_name
                existing_step.normalized_stage = step_rec.normalized_stage
                existing_step.status = step_rec.status
                existing_step.conclusion = step_rec.conclusion
                existing_step.started_at = step_rec.started_at
                existing_step.completed_at = step_rec.completed_at
                existing_step.duration_seconds = step_rec.duration_seconds
                existing_step.synced_at = now
                steps_updated += 1
            else:
                new_step = PipelineStepModel.from_pydantic(
                    step_rec,
                    run_id=run_id,
                    job_id=job_model.id,
                )
                new_step.synced_at = now
                db.add(new_step)
                steps_inserted += 1

        if prune_missing and existing_steps:
            for stale_step in existing_steps:
                if stale_step.step_number not in deduped_steps:
                    db.delete(stale_step)

    if prune_missing and existing_jobs and (len(deduped_jobs) > 0 or allow_empty_reconcile):
        for stale_job in existing_jobs:
            if stale_job not in kept_jobs:
                db.delete(stale_job)

    db.flush()
    unloaded = sa_inspect(run_model).unloaded
    attrs_to_expire = [attr for attr in ("jobs", "steps") if attr not in unloaded]
    if attrs_to_expire:
        db.expire(run_model, attrs_to_expire)

    return {
        "jobs_inserted": jobs_inserted,
        "jobs_updated": jobs_updated,
        "steps_inserted": steps_inserted,
        "steps_updated": steps_updated,
        "jobs_synced": jobs_inserted + jobs_updated,
        "steps_synced": steps_inserted + steps_updated,
    }


def _sync_run_jobs_and_steps(
    db: Session,
    run_model: PipelineRunModel,
    record: PipelineRunRecord,
    *,
    should_sync_jobs: bool,
    prune_missing: bool,
) -> Tuple[int, int]:
    """Idempotently upsert jobs and steps for one run inside the active transaction."""
    if not should_sync_jobs:
        return 0, 0
    stats = _upsert_run_job_children(
        db=db,
        run_model=run_model,
        run_id=record.run_id,
        jobs=record.jobs,
        prune_missing=prune_missing,
        allow_empty_reconcile=prune_missing,
    )
    return stats["jobs_synced"], stats["steps_synced"]


def sync_run_job_telemetry(
    db: Session,
    run_model: PipelineRunModel,
    jobs: List[PipelineJobRecord],
    *,
    prune_missing: bool = True,
    allow_empty_reconcile: bool = False,
) -> Dict[str, int]:
    """Persist child job/step telemetry for an existing PipelineRunModel without modifying pipeline_runs."""
    if run_model is None:
        raise ValueError("Cannot persist job telemetry without an existing PipelineRunModel.")
    return _upsert_run_job_children(
        db=db,
        run_model=run_model,
        run_id=run_model.run_id,
        jobs=jobs,
        prune_missing=prune_missing,
        allow_empty_reconcile=allow_empty_reconcile,
    )


def preview_run_job_telemetry_diff(
    db: Session,
    run_id: int,
    jobs: List[PipelineJobRecord],
) -> Dict[str, int]:
    """Estimate job and step insert/update counts for a run without mutating database state."""
    deduped_jobs = _dedupe_job_records(jobs)
    if not deduped_jobs:
        return {
            "jobs_inserted": 0,
            "jobs_updated": 0,
            "steps_inserted": 0,
            "steps_updated": 0,
        }

    (
        _existing_jobs,
        existing_by_job_key,
        existing_by_github_id,
        legacy_jobs_by_identity,
    ) = _load_existing_job_lookups(db, run_id)

    matched_jobs: Set[PipelineJobModel] = set()
    matched_job_ids: List[int] = []
    job_matches: List[Tuple[PipelineJobRecord, Optional[PipelineJobModel]]] = []

    for target_job_key, job_rec in deduped_jobs.items():
        existing_job = _match_existing_job(
            job_rec,
            target_job_key,
            existing_by_job_key,
            existing_by_github_id,
            legacy_jobs_by_identity,
            matched_jobs,
        )
        if existing_job is not None:
            matched_jobs.add(existing_job)
            matched_job_ids.append(existing_job.id)
        job_matches.append((job_rec, existing_job))

    step_numbers_by_job_id: Dict[int, Set[int]] = {}
    if matched_job_ids:
        existing_steps = (
            db.query(PipelineStepModel.job_id, PipelineStepModel.step_number)
            .filter(PipelineStepModel.job_id.in_(matched_job_ids))
            .all()
        )
        for job_id_val, step_num in existing_steps:
            step_numbers_by_job_id.setdefault(job_id_val, set()).add(step_num)

    jobs_inserted = 0
    jobs_updated = 0
    steps_inserted = 0
    steps_updated = 0

    for job_rec, existing_job in job_matches:
        deduped_steps = {step.step_number: step for step in job_rec.steps}
        if existing_job is not None:
            jobs_updated += 1
            known_step_nums = step_numbers_by_job_id.get(existing_job.id, set())
            for step_num in deduped_steps:
                if step_num in known_step_nums:
                    steps_updated += 1
                else:
                    steps_inserted += 1
        else:
            jobs_inserted += 1
            steps_inserted += len(deduped_steps)

    return {
        "jobs_inserted": jobs_inserted,
        "jobs_updated": jobs_updated,
        "steps_inserted": steps_inserted,
        "steps_updated": steps_updated,
    }


def _upsert_pipeline_run_with_stats(
    db: Session,
    record: PipelineRunRecord,
    *,
    replace_jobs: Optional[bool] = None,
) -> Tuple[PipelineRunModel, bool, int, int]:
    """Insert or update a pipeline run and its child telemetry in the active session."""
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
        run_model = existing
        was_created = False
    else:
        run_model = PipelineRunModel.from_pydantic(record)
        db.add(run_model)
        was_created = True

    should_sync_jobs, prune_missing = _resolve_job_sync_policy(record, replace_jobs)
    jobs_synced, steps_synced = _sync_run_jobs_and_steps(
        db,
        run_model,
        record,
        should_sync_jobs=should_sync_jobs,
        prune_missing=prune_missing,
    )
    return run_model, was_created, jobs_synced, steps_synced


def upsert_pipeline_run(
    db: Session,
    record: PipelineRunRecord,
    *,
    replace_jobs: Optional[bool] = None,
) -> Tuple[PipelineRunModel, bool]:
    """Insert or update a pipeline run and its child job/step telemetry without duplicates.

    Args:
        db: Active database session.
        record: PipelineRunRecord to persist.
        replace_jobs: Optional override controlling child replacement/pruning.

    Returns:
        Tuple of (model_instance, was_created_boolean).
    """
    run_model, was_created, _, _ = _upsert_pipeline_run_with_stats(
        db,
        record,
        replace_jobs=replace_jobs,
    )
    return run_model, was_created


def upsert_pipeline_runs(
    db: Session,
    records: List[PipelineRunRecord],
    *,
    replace_jobs: Optional[bool] = None,
) -> Dict[str, int]:
    """Transactionally persist a batch of PipelineRunRecord instances without duplicates.

    Args:
        db: Active database session.
        records: List of pipeline run records.
        replace_jobs: Optional override controlling child replacement/pruning.

    Returns:
        Dict with count of total, inserted, updated, jobs_synced, and steps_synced records.
    """
    inserted = 0
    updated = 0
    jobs_synced = 0
    steps_synced = 0

    try:
        for record in records:
            _, is_created, run_jobs_synced, run_steps_synced = _upsert_pipeline_run_with_stats(
                db,
                record,
                replace_jobs=replace_jobs,
            )
            if is_created:
                inserted += 1
            else:
                updated += 1
            jobs_synced += run_jobs_synced
            steps_synced += run_steps_synced

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "total": len(records),
        "inserted": inserted,
        "updated": updated,
        "jobs_synced": jobs_synced,
        "steps_synced": steps_synced,
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
    include_telemetry: bool = False,
    conclusions: Optional[Tuple[str, ...]] = None,
) -> List[PipelineRunModel]:
    """Query stored pipeline runs with filtering, pagination, and optional telemetry loading.

    Args:
        db: Active database session.
        limit: Max records to return.
        offset: Record offset for pagination.
        repository: Filter by repository name.
        branch: Filter by Git branch.
        status: Filter by run status.
        conclusion: Filter by single run conclusion.
        workflow_name: Filter by workflow name.
        failure_type: Filter by failure category.
        include_telemetry: When True, eagerly load nested jobs and steps via selectinload.
        conclusions: Optional tuple of allowed conclusion values.

    Returns:
        List of matching PipelineRunModel instances.
    """
    query = db.query(PipelineRunModel)

    if include_telemetry:
        query = query.options(
            selectinload(PipelineRunModel.jobs).selectinload(PipelineJobModel.steps)
        )

    if repository:
        query = query.filter(PipelineRunModel.repository == repository)
    if branch:
        query = query.filter(PipelineRunModel.branch == branch)
    if status:
        query = query.filter(PipelineRunModel.status == status)
    if conclusion:
        query = query.filter(PipelineRunModel.conclusion == conclusion)
    if conclusions is not None:
        conclusion_values = tuple(dict.fromkeys(c for c in conclusions if c))
        if not conclusion_values:
            return []
        query = query.filter(PipelineRunModel.conclusion.in_(conclusion_values))
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


def get_completed_run_ids_with_jobs(
    db: Session,
    repository: Optional[str] = None,
) -> Set[int]:
    """Return run IDs of completed runs that already have stored job telemetry."""
    query = (
        db.query(PipelineRunModel.run_id)
        .join(PipelineJobModel, PipelineJobModel.run_id == PipelineRunModel.run_id)
        .filter(PipelineRunModel.status == "completed")
    )
    if repository:
        query = query.filter(PipelineRunModel.repository == repository)
    return {row[0] for row in query.distinct().all()}


def get_runs_for_backfill(
    db: Session,
    *,
    repository: Optional[str] = None,
    run_ids: Optional[List[int]] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[int, List[PipelineRunModel]]:
    """Query existing PipelineRunModel rows for targeted historical job telemetry backfill."""
    query = db.query(PipelineRunModel)
    if repository:
        repo_slug = repository.strip()
        if repo_slug:
            query = query.filter(PipelineRunModel.repository == repo_slug)

    if run_ids is not None:
        unique_run_ids = list(dict.fromkeys(int(rid) for rid in run_ids))
        if not unique_run_ids:
            return 0, []
        query = query.filter(PipelineRunModel.run_id.in_(unique_run_ids))

    total_matched = query.count()
    bounded_limit = max(1, min(int(limit), 500))
    bounded_offset = max(0, int(offset))
    runs = (
        query.order_by(PipelineRunModel.created_at.desc(), PipelineRunModel.run_id.desc())
        .offset(bounded_offset)
        .limit(bounded_limit)
        .all()
    )
    return total_matched, runs


def get_jobs_for_run(
    db: Session,
    run_id: int,
) -> List[PipelineJobModel]:
    """Retrieve stored job records for a given run ordered deterministically."""
    return (
        db.query(PipelineJobModel)
        .filter(PipelineJobModel.run_id == run_id)
        .order_by(PipelineJobModel.id.asc())
        .all()
    )


def get_steps_for_run(
    db: Session,
    run_id: int,
) -> List[PipelineStepModel]:
    """Retrieve stored step records for a given run ordered by job_id and step_number."""
    return (
        db.query(PipelineStepModel)
        .filter(PipelineStepModel.run_id == run_id)
        .order_by(PipelineStepModel.job_id.asc(), PipelineStepModel.step_number.asc())
        .all()
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
