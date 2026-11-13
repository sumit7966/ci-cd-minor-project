"""FastAPI router for CI/CD pipeline execution run queries, syncing, and statistics."""

import os
from typing import Any, Dict, List, Optional
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query, status
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

# pyrefly: ignore [missing-import]
from ..data_collection.backfill import _sanitize_error_detail, backfill_job_telemetry
# pyrefly: ignore [missing-import]
from ..data_collection.github_collector import (
    GitHubAPIError,
    MissingTokenError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
# pyrefly: ignore [missing-import]
from ..data_collection.schemas import PipelineRunRecord
# pyrefly: ignore [missing-import]
from ..data_collection.storage import sync_github_runs_to_db
# pyrefly: ignore [missing-import]
from ..db.repository import (
    get_pipeline_run_by_id,
    get_pipeline_runs,
    get_pipeline_stats,
)
# pyrefly: ignore [missing-import]
from ..db.session import get_db

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


@router.post("/sync", summary="Collect and sync workflow runs to PostgreSQL")
def sync_pipeline_runs(
    limit: int = Query(10, ge=1, le=100, description="Number of workflow runs to fetch"),
    use_mock: bool = Query(False, description="Use realistic offline mock data"),
    repository: Optional[str] = Query(None, description="Repository in 'owner/repo' format"),
    fetch_jobs: bool = Query(True, description="Fetch job and step execution telemetry when available"),
    refresh_jobs: bool = Query(False, description="Re-fetch job telemetry even for completed runs already stored"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Collect recent workflow execution runs from GitHub Actions and persist to PostgreSQL.

    Avoids duplicates by updating existing records or creating new ones based on run_id.
    """
    engine = db.get_bind()
    if "mode=ro" in str(getattr(engine, "url", "")).lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Database is operating in read-only demonstration mode. Modifying records is disabled.",
        )

    try:
        result = sync_github_runs_to_db(
            db=db,
            repository=repository,
            limit=limit,
            use_mock=use_mock,
            fetch_jobs=fetch_jobs,
            refresh_jobs=refresh_jobs,
        )
        jobs_synced = result.get("jobs_synced", 0)
        steps_synced = result.get("steps_synced", 0)
        return {
            "status": "success",
            "message": f"Successfully synced {result['total_fetched']} runs ({result['inserted']} new, {result['updated']} updated)",
            "jobs_synced": jobs_synced,
            "steps_synced": steps_synced,
            "data": result,
        }
    except MissingTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(err),
        ) from err
    except RepositoryNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        ) from err
    except RateLimitExceededError as err:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(err),
        ) from err
    except GitHubAPIError as err:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(err),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred during pipeline run sync: {str(err)}",
        ) from err


@router.post(
    "/backfill-telemetry",
    summary="Backfill historical job and step telemetry for existing stored pipeline runs",
)
def backfill_pipeline_telemetry(
    dry_run: bool = Query(
        True,
        description="Preview backfill plan without persisting to PostgreSQL (set false to apply)",
    ),
    probe_github: bool = Query(
        False,
        description="When dry_run=true, execute read-only GitHub job fetches to estimate inserts/updates",
    ),
    repository: Optional[str] = Query(
        None,
        description="Filter stored target runs by repository slug ('owner/repo')",
    ),
    run_ids: Optional[List[int]] = Query(
        None,
        description="Optional explicit GitHub run_id values to backfill",
    ),
    limit: int = Query(
        100,
        ge=1,
        le=500,
        description="Maximum number of stored runs to evaluate in this batch",
    ),
    offset: int = Query(0, ge=0, description="Pagination offset across matching stored runs"),
    refresh_jobs: bool = Query(
        False,
        description="Re-fetch and reconcile jobs/steps for completed runs that already have telemetry",
    ),
    reconcile_empty: bool = Query(
        False,
        description="Allow confirmed complete empty job responses to prune existing stored child rows",
    ),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Safely backfill GitHub Actions job and step telemetry for existing pipeline_runs rows.

    Operational restriction: Intended for internal/local administrative use only because
    no FastAPI user-authentication middleware is configured in this application. Write
    execution (``dry_run=false``) requires ``GITHUB_TOKEN`` in the backend process environment.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not dry_run:
        engine = db.get_bind()
        if "mode=ro" in str(getattr(engine, "url", "")).lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Database is operating in read-only demonstration mode. Telemetry backfill writes are disabled.",
            )

    try:
        result = backfill_job_telemetry(
            db=db,
            repository=repository,
            run_ids=run_ids,
            limit=limit,
            offset=offset,
            refresh_jobs=refresh_jobs,
            dry_run=dry_run,
            probe_github=probe_github,
            reconcile_empty=reconcile_empty,
        )
        return {
            "status": "partial" if result["stopped_early"] else "success",
            **result,
        }
    except MissingTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_sanitize_error_detail(str(err), token),
        ) from err
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_sanitize_error_detail(str(err), token),
        ) from err
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "An unexpected error occurred during historical telemetry backfill: "
                f"{_sanitize_error_detail(str(err), token)}"
            ),
        ) from err


@router.get("/stats", summary="Get aggregated pipeline run execution statistics")
def get_pipeline_statistics(
    repository: Optional[str] = Query(None, description="Filter stats by repository"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve statistical aggregations across stored pipeline executions."""
    return get_pipeline_stats(db=db, repository=repository)


@router.get("", response_model=List[PipelineRunRecord], summary="List stored pipeline runs")
@router.get("/", response_model=List[PipelineRunRecord], include_in_schema=False)
def list_pipeline_runs(
    limit: int = Query(50, ge=1, le=100, description="Maximum number of runs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    repository: Optional[str] = Query(None, description="Filter by repository slug"),
    branch: Optional[str] = Query(None, description="Filter by branch name"),
    status: Optional[str] = Query(None, description="Filter by execution status"),
    conclusion: Optional[str] = Query(None, description="Filter by conclusion"),
    workflow_name: Optional[str] = Query(None, description="Filter by workflow name"),
    failure_type: Optional[str] = Query(None, description="Filter by failure type"),
    db: Session = Depends(get_db),
) -> List[PipelineRunRecord]:
    """Retrieve pipeline run records with optional filtering and pagination."""
    models = get_pipeline_runs(
        db=db,
        limit=limit,
        offset=offset,
        repository=repository,
        branch=branch,
        status=status,
        conclusion=conclusion,
        workflow_name=workflow_name,
        failure_type=failure_type,
    )
    return [m.to_pydantic() for m in models]


@router.get("/{run_id}", response_model=PipelineRunRecord, summary="Get single pipeline run by run_id")
def get_single_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> PipelineRunRecord:
    """Retrieve a single pipeline run by its unique GitHub Actions run ID."""
    model = get_pipeline_run_by_id(db=db, run_id=run_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run with run_id {run_id} was not found.",
        )
    return model.to_pydantic()
