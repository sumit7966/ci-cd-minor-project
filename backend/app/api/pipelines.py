"""FastAPI router for CI/CD pipeline execution run queries, syncing, and statistics."""

from typing import Any, Dict, List, Optional
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query, status
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

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
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Collect recent workflow execution runs from GitHub Actions and persist to PostgreSQL.

    Avoids duplicates by updating existing records or creating new ones based on run_id.
    """
    try:
        result = sync_github_runs_to_db(
            db=db,
            repository=repository,
            limit=limit,
            use_mock=use_mock,
        )
        return {
            "status": "success",
            "message": f"Successfully synced {result['total_fetched']} runs ({result['inserted']} new, {result['updated']} updated)",
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
