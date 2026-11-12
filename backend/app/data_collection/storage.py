"""Storage bridge connecting GitHub Actions collector to PostgreSQL database."""

import os
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from .github_collector import (
    GitHubWorkflowRunCollector,
)
from .schemas import PipelineRunRecord


def save_pipeline_runs_to_db(
    db: Session,
    records: List[PipelineRunRecord],
) -> Dict[str, int]:
    """Persist a list of PipelineRunRecord instances to the database without duplicates."""
    from ..db.repository import upsert_pipeline_runs
    return upsert_pipeline_runs(db, records)


def sync_github_runs_to_db(
    db: Session,
    repository: Optional[str] = None,
    github_token: Optional[str] = None,
    limit: int = 10,
    use_mock: bool = False,
    fetch_jobs: bool = True,
    refresh_jobs: bool = False,
) -> Dict[str, Any]:
    """Collect workflow runs from GitHub Actions and persist them to PostgreSQL.

    Avoids duplicates by updating existing records or inserting new ones.

    Args:
        db: Active database session.
        repository: Repository in 'owner/repo' format. Defaults to env var.
        github_token: GitHub API token. Defaults to env var.
        limit: Number of runs to fetch (1-100).
        use_mock: Whether to fetch realistic mock data instead of live network call.
        fetch_jobs: Whether to collect job and step telemetry when available.
        refresh_jobs: Whether to re-fetch job telemetry for completed runs already stored.

    Returns:
        Summary dict containing counts and synced records.
    """
    from ..db.repository import get_completed_run_ids_with_jobs

    # Resolve repository and token strictly from parameter or environment variables
    repo = (repository.strip() if repository and repository.strip() else None) or os.getenv("GITHUB_REPOSITORY")
    token = github_token or os.getenv("GITHUB_TOKEN")
    collector = GitHubWorkflowRunCollector(repository=repo, github_token=token)

    if use_mock:
        records = collector.get_mock_workflow_runs()[:limit]
        if not fetch_jobs:
            for record in records:
                record.jobs = []
                record._jobs_complete = False
    else:
        skip_ids = (
            get_completed_run_ids_with_jobs(db, repository=collector.repository)
            if (fetch_jobs and not refresh_jobs)
            else set()
        )
        records = collector.fetch_workflow_runs(
            limit=limit,
            fetch_jobs=fetch_jobs,
            skip_job_fetch_run_ids=skip_ids,
        )

    sync_result = save_pipeline_runs_to_db(db, records)

    return {
        "repository": repo,
        "total_fetched": len(records),
        "inserted": sync_result["inserted"],
        "updated": sync_result["updated"],
        "jobs_synced": sync_result["jobs_synced"],
        "steps_synced": sync_result["steps_synced"],
        "records": [r.model_dump() for r in records],
    }
