"""Storage bridge connecting GitHub Actions collector to PostgreSQL database."""

import os
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from .github_collector import (
    GitHubWorkflowRunCollector,
    collect_workflow_runs,
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
) -> Dict[str, Any]:
    """Collect workflow runs from GitHub Actions and persist them to PostgreSQL.

    Avoids duplicates by updating existing records or inserting new ones.

    Args:
        db: Active database session.
        repository: Repository in 'owner/repo' format. Defaults to env var.
        github_token: GitHub API token. Defaults to env var.
        limit: Number of runs to fetch (1-100).
        use_mock: Whether to fetch realistic mock data instead of live network call.

    Returns:
        Summary dict containing counts and synced records.
    """
    # Resolve repository and token strictly from parameter or environment variables
    repo = (repository.strip() if repository and repository.strip() else None) or os.getenv("GITHUB_REPOSITORY")
    token = github_token or os.getenv("GITHUB_TOKEN")
    collector = GitHubWorkflowRunCollector(repository=repo, github_token=token)

    if use_mock:
        records = collector.get_mock_workflow_runs()[:limit]
    else:
        records = collector.fetch_workflow_runs(limit=limit)

    sync_result = save_pipeline_runs_to_db(db, records)

    return {
        "repository": repo,
        "total_fetched": len(records),
        "inserted": sync_result["inserted"],
        "updated": sync_result["updated"],
        "records": [r.model_dump() for r in records],
    }
